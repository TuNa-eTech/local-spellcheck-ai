from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from soatvan.entrypoints import sidecar as sidecar_module
from soatvan.entrypoints.sidecar import (
    Sidecar,
    _boolean_param,
    _custom_prompt,
    _ignored_words,
    _log_dev_exception,
    safe_message,
    validate_request,
)
from soatvan.workflow import ProcessResult


def test_handshake_and_protocol_mismatch(tmp_path: Path) -> None:
    environment = {**os.environ, "LOCALAPPDATA": str(tmp_path)}
    process = subprocess.Popen(
        [sys.executable, "-m", "soatvan.entrypoints.sidecar"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    assert process.stdin and process.stdout
    process.stdin.write(
        json.dumps({"v": 1, "id": "a", "method": "engine.hello", "params": {}}) + "\n"
    )
    process.stdin.flush()
    hello = json.loads(process.stdout.readline())
    assert hello["ok"] is True
    assert hello["result"]["protocol"] == 1

    process.stdin.write(
        json.dumps({"v": 99, "id": "b", "method": "engine.hello", "params": {}}) + "\n"
    )
    process.stdin.flush()
    mismatch = json.loads(process.stdout.readline())
    assert mismatch["error"]["code"] == "PROTOCOL_MISMATCH"
    process.stdin.close()
    process.wait(timeout=5)


def test_hello_advertises_full_review_with_the_model_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sidecar_module, "runtime_available", lambda: True)
    capabilities = Sidecar().hello({})["capabilities"]
    assert "model_classifier" in capabilities
    assert "model_full_review" in capabilities


def test_custom_rule_protocol_crud_and_legacy_dictionary_is_not_public(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SOATVAN_DATA_DIR", str(tmp_path))
    engine = Sidecar()
    assert "custom_rules" in engine.hello({})["capabilities"]
    assert "dictionary" not in engine.hello({})["capabilities"]
    assert engine.dispatch("custom_rule.list", {}) == {"entries": []}

    created = engine.dispatch(
        "custom_rule.upsert",
        {"prompt": '  Luôn giữ nguyên “SoátVăn” và cụm \'AI-first\'.  '},
    )
    assert created["prompt"] == 'Luôn giữ nguyên “SoátVăn” và cụm \'AI-first\'.'
    assert engine.dispatch("custom_rule.list", {}) == {"entries": [created]}

    updated = engine.dispatch(
        "custom_rule.upsert",
        {"id": created["id"], "prompt": "Dùng giọng văn hành chính."},
    )
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] > created["updated_at"]
    assert engine.dispatch("custom_rule.delete", {"id": created["id"]}) == {
        "deleted": True
    }
    assert engine.dispatch("custom_rule.delete", {"id": created["id"]}) == {
        "deleted": False
    }

    with pytest.raises(ValueError, match="METHOD_NOT_FOUND"):
        engine.dispatch("dictionary.list", {})


def test_custom_prompt_transport_limit_and_new_errors_have_safe_messages() -> None:
    assert _custom_prompt("x" * 4_200) == "x" * 4_200
    with pytest.raises(ValueError, match="CUSTOM_PROMPT_TOO_LONG"):
        _custom_prompt("x" * 4_201)

    assert safe_message("DOCUMENT_FINDINGS_NOT_EXPORTABLE") == (
        "Kh\u00f4ng th\u1ec3 g\u1eafn c\u00e1c c\u1ea3nh b\u00e1o v\u00e0o c\u1ea5u tr\u00fac t\u00e0i li\u1ec7u n\u00e0y."
    )
    assert safe_message("MODEL_FULL_REVIEW_NOT_APPROVED") == (
        "Model n\u00e0y ch\u01b0a \u0111\u01b0\u1ee3c ph\u00ea duy\u1ec7t \u0111\u1ec3 r\u00e0 so\u00e1t to\u00e0n v\u0103n."
    )
    assert safe_message("INCLUDE_RULE_FINDINGS_REQUIRES_FULL_REVIEW") == (
        "Quy tắc tự động chỉ có thể được bổ sung khi bật AI rà soát toàn văn."
    )
    assert safe_message("CUSTOM_PROMPT_CONTEXT_EXCEEDED") == (
        "Quy tắc riêng quá dài so với cửa sổ ngữ cảnh đang dùng."
    )


def test_exception_details_are_logged_only_when_dev_logging_is_enabled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    error = ValueError("MODEL_FULL_REVIEW_FAILED")
    monkeypatch.delenv("SOATVAN_DEV_LOG", raising=False)
    _log_dev_exception("job id=job-1", error, "MODEL_FULL_REVIEW_FAILED")
    assert capsys.readouterr().err == ""

    monkeypatch.setenv("SOATVAN_DEV_LOG", "1")
    _log_dev_exception("job id=job-1", error, "MODEL_FULL_REVIEW_FAILED")
    logged = capsys.readouterr().err
    assert "[engine-error] job id=job-1 failed" in logged
    assert "code=MODEL_FULL_REVIEW_FAILED type=ValueError" in logged
    assert "ValueError: MODEL_FULL_REVIEW_FAILED" in logged


@pytest.mark.parametrize("name", ["use_model", "full_review", "include_rule_findings"])
def test_model_mode_transport_flags_require_booleans(name: str) -> None:
    assert _boolean_param({}, name) is False
    assert _boolean_param({name: True}, name) is True
    with pytest.raises(ValueError, match="INVALID_PARAMS"):
        _boolean_param({name: "false"}, name)


def test_job_start_is_async_and_emits_terminal_event(make_docx, tmp_path: Path) -> None:
    source = make_docx([["Văn bản sát nhập."]])
    output = tmp_path / "temporary.docx"
    environment = {**os.environ, "LOCALAPPDATA": str(tmp_path / "data")}
    process = subprocess.Popen(
        [sys.executable, "-m", "soatvan.entrypoints.sidecar"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    assert process.stdin and process.stdout
    request = {
        "v": 1,
        "id": "start",
        "method": "job.start",
        "params": {
            "job_id": "job-1",
            "source_path": str(source),
            "temporary_output_path": str(output),
            "preset": "standard",
        },
    }
    process.stdin.write(json.dumps(request) + "\n")
    process.stdin.flush()
    accepted = False
    terminal = None
    progress: list[int] = []
    for _ in range(12):
        frame = json.loads(process.stdout.readline())
        if frame.get("id") == "start":
            accepted = frame["result"]["accepted"]
        if frame.get("event") in {"job.completed", "job.no_findings", "job.failed"}:
            terminal = frame
        if frame.get("event") == "job.progress":
            progress.append(frame["data"]["percent"])
        if accepted and terminal:
            break
    assert accepted is True
    assert terminal and terminal["event"] == "job.completed"
    assert terminal["data"]["counts"] == {
        "category": {"spelling": 1},
        "origin": {"rule": 1},
    }
    assert progress == sorted(progress)
    assert progress == [10, 35, 72, 88, 100]
    assert output.is_file()
    process.stdin.close()
    process.wait(timeout=5)


@pytest.mark.parametrize(
    "frame",
    [
        [],
        {"v": 1, "id": "", "method": "engine.hello", "params": {}},
        {"v": 1, "id": "x", "method": "engine.hello", "params": {}, "extra": True},
        {"v": 1, "id": "x", "method": "engine.hello", "params": []},
    ],
)
def test_invalid_contract_frames_are_rejected(frame: object) -> None:
    with pytest.raises(ValueError, match="INVALID_FRAME"):
        validate_request(frame)


def test_frame_limit_is_stable_and_engine_recovers(tmp_path: Path) -> None:
    environment = {**os.environ, "LOCALAPPDATA": str(tmp_path)}
    process = subprocess.Popen(
        [sys.executable, "-m", "soatvan.entrypoints.sidecar"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    assert process.stdin and process.stdout
    process.stdin.write(b"{" + b"x" * (1024 * 1024) + b"}\n")
    process.stdin.flush()
    too_large = json.loads(process.stdout.readline())
    assert too_large["error"]["code"] == "FRAME_TOO_LARGE"
    process.stdin.write(b'{"v":1,"id":"ok","method":"engine.hello","params":{}}\n')
    process.stdin.flush()
    assert json.loads(process.stdout.readline())["result"]["protocol"] == 1
    process.stdin.close()
    process.wait(timeout=5)


def test_session_ignore_words_boundary_is_strict() -> None:
    assert _ignored_words([" SoátVăn ", "SoátVăn"]) == frozenset({"SoátVăn"})
    with pytest.raises(ValueError, match="SESSION_DICTIONARY_INVALID"):
        _ignored_words(["bad\nword"])


def test_model_remove_deactivates_runtime_without_owning_filesystem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    engine = Sidecar()
    deactivated = False

    def deactivate() -> None:
        nonlocal deactivated
        deactivated = True

    monkeypatch.setattr(engine.models, "deactivate", deactivate)
    assert engine.dispatch("model.remove", {}) == {"deactivated": True}
    assert deactivated is True


def test_cancel_emits_one_terminal_failure_and_cleans_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    started = threading.Event()
    frames: list[dict[str, object]] = []

    class BlockingProcessor:
        def execute(self, request, progress, token):
            del request, progress
            started.set()
            while True:
                token.raise_if_cancelled()
                time.sleep(0.005)

    engine = Sidecar()
    engine.processor = BlockingProcessor()  # type: ignore[assignment]
    monkeypatch.setattr(sidecar_module, "emit", frames.append)
    assert engine.start_job(
        {
            "job_id": "cancel-me",
            "source_path": str(tmp_path / "source.docx"),
            "temporary_output_path": str(tmp_path / "temporary.docx"),
            "preset": "standard",
        }
    )["accepted"]
    assert started.wait(timeout=1)
    assert engine.cancel_job({"job_id": "cancel-me"}) == {"cancelled": True}
    deadline = time.monotonic() + 2
    while "cancel-me" in engine.jobs and time.monotonic() < deadline:
        time.sleep(0.01)
    terminals = [frame for frame in frames if frame.get("event") == "job.failed"]
    assert len(terminals) == 1
    assert terminals[0]["data"]["code"] == "JOB_CANCELLED"  # type: ignore[index]
    assert "cancel-me" not in engine.jobs


def test_partial_review_without_findings_is_not_emitted_as_no_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    frames: list[dict[str, object]] = []

    class PartialProcessor:
        def execute(self, request, progress, token):
            del request, progress, token
            return ProcessResult(
                0,
                None,
                {},
                {
                    "status": "partial",
                    "total_chunks": 2,
                    "reviewed_chunks": 1,
                    "failed_chunks": 1,
                },
            )

    engine = Sidecar()
    engine.processor = PartialProcessor()  # type: ignore[assignment]
    monkeypatch.setattr(sidecar_module, "emit", frames.append)
    assert engine.start_job(
        {
            "job_id": "partial-review",
            "source_path": str(tmp_path / "source.docx"),
            "temporary_output_path": str(tmp_path / "temporary.docx"),
            "preset": "standard",
        }
    )["accepted"]
    deadline = time.monotonic() + 2
    while "partial-review" in engine.jobs and time.monotonic() < deadline:
        time.sleep(0.01)
    terminals = [
        frame
        for frame in frames
        if frame.get("event") in {"job.completed", "job.no_findings", "job.failed"}
    ]
    assert [frame["event"] for frame in terminals] == ["job.completed"]
