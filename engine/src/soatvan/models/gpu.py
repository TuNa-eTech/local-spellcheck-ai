"""GPU offload policy for the local llama.cpp runtime.

Which backends exist at all is decided when llama.cpp is compiled: the Windows
release ships a CUDA build plus the CUDA redistributables, macOS gets Metal by
default. What is left to decide here, at runtime, is whether this particular
machine may actually *use* the GPU the binary was built for — an RTX machine
should offload every layer, a machine whose driver is missing or broken must
stay on CPU without dragging the whole feature down with it.

Two things make that decision: the user override (``SOATVAN_GPU_OFFLOAD``) and
the crash guard below.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from soatvan import __version__
from soatvan.paths import DATA_DIR_ENV, local_data_dir

OFFLOAD_ENV = "SOATVAN_GPU_OFFLOAD"
GUARD_FILENAME = "gpu-offload.json"

__all__ = [
    "DATA_DIR_ENV",
    "OffloadGuard",
    "backend_names",
    "backend_report",
    "build_id",
    "local_data_dir",
    "offload_allowed",
    "offload_mode",
]

#: Bump when the marker's shape changes; older records are ignored, not migrated.
GUARD_SCHEMA_VERSION = 2
#: How many *inferred* crashes (a marker left at "loading") it takes to stop
#: trying the GPU. One is not enough: killing the app mid-load leaves the same
#: trace as a driver crash, and the sidecar is in a job object that dies with
#: the window.
CRASH_LIMIT = 2

_MODES = ("auto", "off", "force")


def offload_mode() -> str:
    """``auto`` (default), ``off`` to stay on CPU, ``force`` to clear the guard."""
    mode = os.environ.get(OFFLOAD_ENV, "auto").strip().lower()
    return mode if mode in _MODES else "auto"


def backend_report(module: Any) -> dict[str, Any]:
    """What the compiled llama.cpp offers and what it found on this machine.

    ``supports_offload`` is false both when the binary has no GPU backend
    compiled in and when it has one that registered zero devices (a CUDA build
    on a machine with no NVIDIA driver), so it answers the only question the
    loader cares about. ``backends`` separates those two cases for diagnostics:
    a Windows release that does not list ``CUDA`` was built wrong.
    """
    info = ""
    printer = getattr(module, "llama_print_system_info", None)
    if callable(printer):
        try:
            raw = printer()
            info = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        except Exception:  # pragma: no cover - defensive around a native call
            info = ""
    supports = False
    probe = getattr(module, "llama_supports_gpu_offload", None)
    if callable(probe):
        try:
            supports = bool(probe())
        except Exception:  # pragma: no cover - defensive around a native call
            supports = False
    return {
        "supports_offload": supports,
        "backends": backend_names(info),
        "system_info": info.strip(),
    }


def build_id(report: dict[str, Any]) -> str:
    """Identify the binary this decision was made about.

    A crash marker is only meaningful for the build that wrote it. Downloading a
    newer portable release — the whole point of which may be a fixed CUDA
    runtime — must not inherit the old build's verdict. The engine version plus
    a digest of llama.cpp's own system-info string covers both halves: a Python
    release and a swapped native library.
    """
    digest = hashlib.sha256(str(report.get("system_info", "")).encode("utf-8")).hexdigest()
    return f"{__version__}:{digest[:12]}"


def backend_names(system_info: str) -> list[str]:
    """Pull the backend names out of ``llama_print_system_info``.

    The native string is one ``NAME : FLAG = value | FLAG = value`` group per
    registered backend, e.g. ``CUDA : ARCHS = 860 | ... | CPU : AVX2 = 1 | ...``.
    """
    names: list[str] = []
    for group in system_info.split("|"):
        head, separator, _ = group.partition(":")
        name = head.strip()
        if separator and name and name not in names:
            names.append(name)
    return names


class OffloadGuard:
    """Remembers a GPU load that took the whole process down with it.

    Loading a model onto the GPU can abort natively — a faulty driver, or a
    VRAM allocation that fails inside an assert — and no ``except`` in Python
    can catch that. The marker written before the attempt survives the crash,
    so the next sidecar start sees it and stays on CPU instead of dying on
    every document the user opens. ``SOATVAN_GPU_OFFLOAD=force`` clears it.
    """

    def __init__(self, state_dir: Path, build: str = "") -> None:
        self._path = state_dir / GUARD_FILENAME
        self._build = build

    def blocked_reason(self) -> str | None:
        """Why offload is blocked, or None when the GPU may be tried."""
        state = self._own_record()
        status = state.get("status")
        if status == "blocked":
            # A caught exception: a definite answer, trusted the first time.
            reason = state.get("reason")
            return str(reason) if reason else "a previous GPU load failed"
        if status == "loading" and self._observed_crashes(state) >= CRASH_LIMIT:
            # Written before a load that never reported back. One of these can
            # just mean the user closed the window, so it takes CRASH_LIMIT.
            return "crashed while loading the model onto the GPU"
        return None

    def begin(self) -> None:
        self._write("loading", "", self._observed_crashes(self._own_record()))

    def succeeded(self) -> None:
        self._write("ok", "", 0)

    def failed(self, reason: str) -> None:
        self._write("blocked", reason, self._crashes(self._own_record()))

    def record_decision(self, offload: bool, reason: str) -> None:
        """Remember the verdict so `model.status` can report it without llama.cpp."""
        self._merge({
            "last_decision": {
                "offload": offload,
                "blocked": not offload and self.blocked_reason() is not None,
                "reason": reason,
            }
        })

    def last_decision(self) -> dict[str, Any] | None:
        """The most recent verdict, whichever build made it, or None if never run."""
        decision = self._read().get("last_decision")
        return decision if isinstance(decision, dict) else None

    def clear(self) -> None:
        with contextlib.suppress(OSError):
            self._path.unlink()

    def _read(self) -> dict[str, Any]:
        try:
            parsed = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _own_record(self) -> dict[str, Any]:
        """The marker, but only when this build wrote it."""
        state = self._read()
        if state.get("v") != GUARD_SCHEMA_VERSION or state.get("build") != self._build:
            return {}
        return state

    @staticmethod
    def _crashes(state: dict[str, Any]) -> int:
        try:
            return max(0, int(state.get("crashes", 0)))
        except (TypeError, ValueError):
            return 0

    def _observed_crashes(self, state: dict[str, Any]) -> int:
        """The tally including the record in hand.

        A marker still sitting at "loading" is itself an attempt that never
        reported back, and it has not been counted yet — the run that would have
        counted it is the one asking now.
        """
        crashes = self._crashes(state)
        return crashes + 1 if state.get("status") == "loading" else crashes

    def _write(self, status: str, reason: str, crashes: int) -> None:
        self._merge({
            "v": GUARD_SCHEMA_VERSION,
            "build": self._build,
            "status": status,
            "reason": reason,
            "crashes": crashes,
            "at": time.time(),
        })

    def _merge(self, fields: dict[str, Any]) -> None:
        payload = {**self._read(), **fields}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            # A read-only state directory must not stop the model from loading;
            # the guard degrades to "always try the GPU".
            pass


def offload_allowed(guard: OffloadGuard, report: dict[str, Any]) -> tuple[bool, str]:
    """Decide whether this run may offload, with the reason for the log."""
    allowed, reason = _offload_verdict(guard, report)
    guard.record_decision(allowed, reason)
    return allowed, reason


def _offload_verdict(guard: OffloadGuard, report: dict[str, Any]) -> tuple[bool, str]:
    mode = offload_mode()
    if mode == "off":
        return False, f"{OFFLOAD_ENV}=off"
    if mode == "force":
        guard.clear()
    else:
        blocked = guard.blocked_reason()
        if blocked is not None:
            return False, f"blocked by the crash guard ({blocked}); set {OFFLOAD_ENV}=force to retry"
    if not report.get("supports_offload"):
        backends = ", ".join(str(name) for name in report.get("backends", ())) or "none"
        return False, f"no GPU device registered (backends: {backends})"
    return True, f"GPU offload available (backends: {', '.join(report.get('backends', ()))})"
