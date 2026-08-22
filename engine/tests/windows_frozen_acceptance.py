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
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
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
        process = subprocess.Popen(
            [str(args.engine)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        frames = Frames(process)
        send(process, {"v": 1, "id": "hello", "method": "engine.hello", "params": {}})
        hello = frames.until(lambda frame: frame.get("id") == "hello")
        assert hello.get("ok") is True
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
        assert process.stdin
        process.stdin.close()
        assert process.wait(timeout=10) == 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
