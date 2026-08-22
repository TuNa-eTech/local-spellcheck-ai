from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


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
    for _ in range(12):
        frame = json.loads(process.stdout.readline())
        if frame.get("id") == "start":
            accepted = frame["result"]["accepted"]
        if frame.get("event") in {"job.completed", "job.no_findings", "job.failed"}:
            terminal = frame
        if accepted and terminal:
            break
    assert accepted is True
    assert terminal and terminal["event"] == "job.completed"
    assert output.is_file()
    process.stdin.close()
    process.wait(timeout=5)
