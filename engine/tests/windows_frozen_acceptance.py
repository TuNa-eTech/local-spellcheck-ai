from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path

CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
ROOT_RELS = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
EMPTY_RELS = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>"""


def make_docx(path: Path, paragraphs: list[str], stored: bool = False) -> None:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    ).encode()
    compression = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(path, "w", compression) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", ROOT_RELS)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", EMPTY_RELS)


class Frames:
    def __init__(self, process: subprocess.Popen[str]) -> None:
        self._queue: queue.Queue[dict[str, object]] = queue.Queue()
        assert process.stdout

        def pump() -> None:
            for line in process.stdout:
                self._queue.put(json.loads(line))

        threading.Thread(target=pump, daemon=True).start()

    def next(self, timeout: float = 15) -> dict[str, object]:
        return self._queue.get(timeout=timeout)

    def until(self, predicate, timeout: float = 30) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self.next(max(0.1, deadline - time.monotonic()))
            if predicate(frame):
                return frame
        raise TimeoutError("expected sidecar frame was not emitted")


def send(process: subprocess.Popen[str], frame: dict[str, object]) -> None:
    assert process.stdin
    process.stdin.write(json.dumps(frame, ensure_ascii=False) + "\n")
    process.stdin.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument(
        "--seq2seq-model-dir",
        type=Path,
        default=None,
        help="If given, additionally verify the frozen sidecar can load and run "
        "the seq2seq speller from this model directory (must contain config.json).",
    )
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    stderr_log = args.artifacts / "frozen-sidecar-stderr.log"
    with tempfile.TemporaryDirectory(prefix="soatvan-windows-") as temporary:
        root = Path(temporary)
        long_folder = root / "Tài liệu kiểm thử có khoảng trắng"
        while len(str(long_folder)) < 270:
            long_folder /= "đường-dẫn-rất-dài-0123456789"
        long_folder.mkdir(parents=True)
        source = long_folder / "nguồn kiểm thử.docx"
        output = long_folder / "kết quả tạm.docx"
        make_docx(source, ["Văn bản sát nhập  nội dung."])
        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        environment = dict(os.environ)
        environment["LOCALAPPDATA"] = str(root / "Local App Data")
        environment["PATH"] = str(Path(os.environ["SYSTEMROOT"]) / "System32")
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        # Drain stderr to a file rather than PIPE: loading torch is verbose enough
        # to fill a 64 KB pipe and deadlock the sidecar, and the log is useful
        # when a bundled dependency is missing.
        stderr_handle = stderr_log.open("wb")
        process = subprocess.Popen(
            [str(args.engine)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_handle,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        frames = Frames(process)
        send(process, {"v": 1, "id": "hello", "method": "engine.hello", "params": {}})
        hello = frames.until(lambda frame: frame.get("id") == "hello")
        assert hello.get("ok") is True
        assert "model_classifier" in hello["result"]["capabilities"]
        assert "model_full_review" in hello["result"]["capabilities"]
        send(
            process,
            {
                "v": 1,
                "id": "start",
                "method": "job.start",
                "params": {
                    "job_id": "frozen-e2e",
                    "source_path": str(source),
                    "temporary_output_path": str(output),
                    "preset": "standard",
                },
            },
        )
        terminal = frames.until(
            lambda frame: frame.get("event")
            in {"job.completed", "job.no_findings", "job.failed"}
        )
        assert terminal.get("event") == "job.completed", terminal
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
        with zipfile.ZipFile(output) as archive:
            assert archive.testzip() is None
            assert "word/comments.xml" in archive.namelist()
        accepted_output = args.artifacts / "frozen-sidecar-output.docx"
        accepted_output.write_bytes(output.read_bytes())

        cancel_source = root / "cancel-source.docx"
        cancel_output = root / "cancel-output.docx"
        make_docx(cancel_source, ["sát nhập " * 150_000], stored=True)
        send(
            process,
            {
                "v": 1,
                "id": "cancel-start",
                "method": "job.start",
                "params": {
                    "job_id": "cancel-e2e",
                    "source_path": str(cancel_source),
                    "temporary_output_path": str(cancel_output),
                    "preset": "standard",
                },
            },
        )
        frames.until(lambda frame: frame.get("id") == "cancel-start")
        send(
            process,
            {"v": 1, "id": "cancel", "method": "job.cancel", "params": {"job_id": "cancel-e2e"}},
        )
        frames.until(lambda frame: frame.get("id") == "cancel")
        cancelled = frames.until(
            lambda frame: frame.get("event")
            in {"job.completed", "job.no_findings", "job.failed"},
            timeout=60,
        )
        assert cancelled.get("event") == "job.failed", cancelled
        assert cancelled["data"]["code"] == "JOB_CANCELLED"  # type: ignore[index]
        assert not cancel_output.exists()

        if args.seq2seq_model_dir is not None:
            _verify_seq2seq(process, frames, args.seq2seq_model_dir, root, stderr_log)

        assert process.stdin
        process.stdin.close()
        assert process.wait(timeout=10) == 0
        stderr_handle.close()
    return 0


def _verify_seq2seq(
    process: subprocess.Popen[str],
    frames: Frames,
    model_dir: Path,
    root: Path,
    stderr_log: Path,
) -> None:
    """Point the frozen sidecar at a real seq2seq model and run one job through it.

    Proves the bundled torch / transformers / sentencepiece / protobuf stack
    actually imports and the tokenizer + model load — the path the rules-only
    checks above never touch.
    """
    if not (model_dir / "config.json").is_file():
        raise SystemExit(f"--seq2seq-model-dir has no config.json: {model_dir}")

    send(
        process,
        {
            "v": 1,
            "id": "s2s-config",
            "method": "seq2seq_config.update",
            "params": {"model_dir": str(model_dir), "is_enabled": True},
        },
    )
    config = frames.until(lambda frame: frame.get("id") == "s2s-config")
    result = config.get("result", {})
    assert result.get("runtime_available") is True, (
        f"frozen sidecar cannot import the seq2seq runtime: {result}"
    )
    assert result.get("is_ready") is True, f"seq2seq model not ready: {result}"

    s2s_source = root / "seq2seq-source.docx"
    s2s_output = root / "seq2seq-output.docx"
    make_docx(s2s_source, ["Toi yeu Viet Nam va tieng Viet."])
    send(
        process,
        {
            "v": 1,
            "id": "s2s-start",
            "method": "job.start",
            "params": {
                "job_id": "seq2seq-e2e",
                "source_path": str(s2s_source),
                "temporary_output_path": str(s2s_output),
                "preset": "standard",
            },
        },
    )
    terminal = frames.until(
        lambda frame: frame.get("event")
        in {"job.completed", "job.no_findings", "job.failed"},
        timeout=300,
    )
    assert terminal.get("event") in {"job.completed", "job.no_findings"}, terminal

    # A job completes even when the seq2seq pass throws (the workflow catches it
    # and drops the pass). Inspect the sidecar's own log — the engine flushes
    # these lines explicitly — to be sure it ran the model rather than skipping.
    log = stderr_log.read_text(encoding="utf-8", errors="replace")
    assert "Seq2Seq pass failed" not in log, (
        "seq2seq loaded-check passed but the pass threw at runtime; see "
        f"{stderr_log}\n--- tail ---\n{log[-2000:]}"
    )
    assert "Running Seq2Seq spell-check pass" in log, (
        f"seq2seq pass never started; see {stderr_log}\n--- tail ---\n{log[-2000:]}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
