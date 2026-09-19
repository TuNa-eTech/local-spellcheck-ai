from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from soatvan.checking.domain import Block
from soatvan.workflow.process import _run_seq2seq_subprocess

FINDING = {
    "id": "document:p0:0:6:seq2seq",
    "category": "spelling",
    "origin": "seq2seq",
    "detector_id": "seq2seq.v1",
    "block_id": "document:p0",
    "start": 0,
    "end": 6,
    "source_text": "sát nhập",
    "suggestion": "sáp nhập",
    "reason": "Từ hoặc cụm từ có thể sai chính tả.",
    "rule_version": "seq2seq@1",
    "confidence": 1.0,
}

WORKER_OK = (
    "import json,sys;"
    "sys.stdin.buffer.read();"
    "sys.stderr.write('worker log\\n');"
    f"sys.stdout.buffer.write(json.dumps({{'findings': [{json.dumps(FINDING)}]}}).encode())"
)
WORKER_FAILS = "import sys;sys.stdin.buffer.read();sys.exit(3)"
WORKER_HANGS = "import sys,time;sys.stdin.buffer.read();time.sleep(60)"


class _Stub:
    def __init__(self, model_dir: Path) -> None:
        self._model_dir = model_dir


class _NoCancellation:
    def raise_if_cancelled(self) -> None:
        pass


def _fake_worker(monkeypatch: pytest.MonkeyPatch, script: str) -> list[subprocess.Popen[bytes]]:
    """Replace the worker module invocation with an inline script."""
    spawned: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def popen(args: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        proc = real_popen([sys.executable, "-c", script], **kwargs)  # type: ignore[arg-type]
        spawned.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", popen)
    return spawned


def test_successful_worker_returns_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Regression: `Popen.returncode` is None until the child is reaped, so the
    # exit status must be read after wait() — otherwise every run raises and the
    # caller silently drops all seq2seq findings.
    _fake_worker(monkeypatch, WORKER_OK)
    findings = _run_seq2seq_subprocess(
        _Stub(tmp_path), [Block("document:p0", "sát nhập")], frozenset(), _NoCancellation()
    )
    assert [item.source_text for item in findings] == ["sát nhập"]
    assert findings[0].suggestion == "sáp nhập"


def test_failing_worker_reports_its_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_worker(monkeypatch, WORKER_FAILS)
    with pytest.raises(RuntimeError, match="exited with code 3"):
        _run_seq2seq_subprocess(
            _Stub(tmp_path), [Block("document:p0", "sát nhập")], frozenset(), _NoCancellation()
        )


def test_cancellation_kills_the_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spawned = _fake_worker(monkeypatch, WORKER_HANGS)

    class _Cancelled:
        def raise_if_cancelled(self) -> None:
            raise RuntimeError("JOB_CANCELLED")

    with pytest.raises(RuntimeError, match="JOB_CANCELLED"):
        _run_seq2seq_subprocess(
            _Stub(tmp_path), [Block("document:p0", "sát nhập")], frozenset(), _Cancelled()
        )
    assert spawned and spawned[0].poll() is not None


def test_worker_exceeding_the_deadline_is_killed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "soatvan.workflow.process.SEQ2SEQ_SUBPROCESS_TIMEOUT_SECONDS", 0
    )
    spawned = _fake_worker(monkeypatch, WORKER_HANGS)
    with pytest.raises(TimeoutError):
        _run_seq2seq_subprocess(
            _Stub(tmp_path), [Block("document:p0", "sát nhập")], frozenset(), _NoCancellation()
        )
    assert spawned and spawned[0].poll() is not None


def test_default_model_dir_prefers_the_portable_bundle_then_the_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A portable release ships <root>/SoatVan.exe beside <root>/engine/…exe.

    The old candidate list had no exe-relative entry at all, and its
    "%LOCALAPPDATA%/SoatVan" guess could never match the host's real data
    directory (%LOCALAPPDATA%/vn.soatvan.desktop).
    """
    from soatvan.workflow import seq2seq_provider

    state_dir = tmp_path / "vn.soatvan.desktop"
    in_state = state_dir / "models" / "vn-spell-correction-small"
    in_state.mkdir(parents=True)
    (in_state / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SOATVAN_DATA_DIR", str(state_dir))
    monkeypatch.setattr(seq2seq_provider, "bundle_root", lambda: None)

    assert seq2seq_provider.find_default_model_dir() == in_state

    portable = tmp_path / "SoatVan-Portable"
    in_bundle = portable / "models" / "vn-spell-correction-small"
    in_bundle.mkdir(parents=True)
    (in_bundle / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(seq2seq_provider, "bundle_root", lambda: portable)

    assert seq2seq_provider.find_default_model_dir() == in_bundle


def test_bundle_root_is_none_unless_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    from soatvan import paths

    monkeypatch.delattr(sys, "frozen", raising=False)
    assert paths.bundle_root() is None

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/SoatVan/engine/soatvan-engine")
    assert paths.bundle_root() == Path("/opt/SoatVan")
