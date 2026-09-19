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
import json
import os
import time
from pathlib import Path
from typing import Any

OFFLOAD_ENV = "SOATVAN_GPU_OFFLOAD"
DATA_DIR_ENV = "SOATVAN_DATA_DIR"
GUARD_FILENAME = "gpu-offload.json"

_MODES = ("auto", "off", "force")


def local_data_dir() -> Path:
    """The per-user state directory the sidecar keeps its databases in."""
    configured = os.environ.get(DATA_DIR_ENV)
    if configured:
        return Path(configured)
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "SoatVan"


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

    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / GUARD_FILENAME

    def blocked_reason(self) -> str | None:
        """Why offload is blocked, or None when the GPU may be tried."""
        state = self._read()
        status = state.get("status")
        if status == "loading":
            # Written before a load that never reported back: the process died.
            return "crashed while loading the model onto the GPU"
        if status == "blocked":
            reason = state.get("reason")
            return str(reason) if reason else "a previous GPU load failed"
        return None

    def begin(self) -> None:
        self._write("loading", "")

    def succeeded(self) -> None:
        self._write("ok", "")

    def failed(self, reason: str) -> None:
        self._write("blocked", reason)

    def clear(self) -> None:
        with contextlib.suppress(OSError):
            self._path.unlink()

    def _read(self) -> dict[str, Any]:
        try:
            parsed = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _write(self, status: str, reason: str) -> None:
        payload = {"status": status, "reason": reason, "at": time.time()}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            # A read-only state directory must not stop the model from loading;
            # the guard degrades to "always try the GPU".
            pass


def offload_allowed(guard: OffloadGuard, report: dict[str, Any]) -> tuple[bool, str]:
    """Decide whether this run may offload, with the reason for the log."""
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
