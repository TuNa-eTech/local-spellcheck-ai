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
