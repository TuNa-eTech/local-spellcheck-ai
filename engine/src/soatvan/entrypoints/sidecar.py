from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

from soatvan import PROTOCOL_VERSION, __version__
from soatvan.checking import Preset, RuleEngine
from soatvan.dictionary import SqliteDictionaryRepository
from soatvan.document import DocxPackage, InvalidDocument
from soatvan.models import ModelRegistry
from soatvan.workflow import ProcessDocument, ProcessRequest

MAX_FRAME = 1024 * 1024
EMIT_LOCK = threading.Lock()


class CancelledError(RuntimeError):
    pass


class Token:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise CancelledError("JOB_CANCELLED")


class Sidecar:
    def __init__(self) -> None:
        local_data = (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "SoatVan"
        )
        self.documents = DocxPackage()
        self.dictionary = SqliteDictionaryRepository(local_data / "dictionary.db")
        self.models = ModelRegistry(local_data / "models")
        self.processor = ProcessDocument(self.documents, self.dictionary, RuleEngine())
        self.jobs: dict[str, Token] = {}

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        handlers = {
            "engine.hello": self.hello,
            "document.inspect": self.inspect,
            "job.start": self.start_job,
            "job.cancel": self.cancel_job,
            "dictionary.list": self.dictionary_list,
            "dictionary.upsert": self.dictionary_upsert,
            "dictionary.delete": self.dictionary_delete,
            "dictionary.import": self.dictionary_import,
            "dictionary.export": self.dictionary_export,
            "model.status": self.model_status,
        }
        if method in {"model.download", "model.import", "model.cancel", "model.remove"}:
            raise ValueError("MODEL_PROVISIONING_OWNED_BY_HOST")
        if method not in handlers:
            raise ValueError("METHOD_NOT_FOUND")
        return handlers[method](params)

    def hello(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_VERSION,
            "engine_version": __version__,
            "capabilities": ["rules", "dictionary", "docx_annotations"],
        }

    def inspect(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.documents.inspect(Path(params["source_path"]))

    def start_job(self, params: dict[str, Any]) -> dict[str, Any]:
        job_id = str(params["job_id"])
        if job_id in self.jobs:
            raise ValueError("JOB_ALREADY_EXISTS")
        token = Token()
        self.jobs[job_id] = token
        threading.Thread(
            target=self._run_job,
            args=(job_id, dict(params), token),
            name=f"soatvan-job-{job_id}",
            daemon=True,
        ).start()
        return {"accepted": True, "job_id": job_id}

    def _run_job(self, job_id: str, params: dict[str, Any], token: Token) -> None:
        temporary_output = Path(params["temporary_output_path"])
        try:
            request = ProcessRequest(
                Path(params["source_path"]),
                temporary_output,
                Preset(params.get("preset", "standard")),
            )

            def progress(stage: str, percent: int, message_code: str) -> None:
                emit(
                    {
                        "v": 1,
                        "event": "job.progress",
                        "data": {
                            "job_id": job_id,
                            "stage": stage,
                            "percent": percent,
                            "message_code": message_code,
                        },
                    }
                )

            result = self.processor.execute(request, progress, token)
            event = "job.completed" if result.output_path else "job.no_findings"
            emit(
                {
                    "v": 1,
                    "event": event,
                    "data": {
                        "job_id": job_id,
                        "temporary_output_path": str(result.output_path)
                        if result.output_path
                        else None,
                        "finding_count": result.finding_count,
                        "counts": result.counts,
                    },
                }
            )
        except Exception as error:
            temporary_output.unlink(missing_ok=True)
            code = error_code(error)
            if code == "ENGINE_INTERNAL_ERROR":
                traceback.print_exc(file=sys.stderr)
            emit(
                {
                    "v": 1,
                    "event": "job.failed",
                    "data": {
                        "job_id": job_id,
                        "code": code,
                        "message": safe_message(code),
                    },
                }
            )
        finally:
            self.jobs.pop(job_id, None)

    def cancel_job(self, params: dict[str, Any]) -> dict[str, Any]:
        token = self.jobs.get(str(params["job_id"]))
        if token:
            token.cancel()
        return {"cancelled": bool(token)}

    def dictionary_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "entries": [
                asdict(entry) for entry in self.dictionary.list(str(params.get("query", "")))
            ]
        }

    def dictionary_upsert(self, params: dict[str, Any]) -> dict[str, Any]:
        return asdict(self.dictionary.upsert(str(params["word"]), str(params.get("note", ""))))

    def dictionary_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"deleted": self.dictionary.delete(str(params["word"]))}

    def dictionary_import(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"count": self.dictionary.import_csv(Path(params["path"]))}

    def dictionary_export(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"count": self.dictionary.export_csv(Path(params["path"]))}

    def model_status(self, _: dict[str, Any]) -> dict[str, Any]:
        return self.models.status()


def emit(frame: dict[str, Any]) -> None:
    with EMIT_LOCK:
        sys.stdout.write(json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def error_code(error: Exception) -> str:
    if isinstance(error, KeyError):
        return "INVALID_PARAMS"
    if isinstance(error, InvalidDocument):
        return str(error)
    if isinstance(error, CancelledError):
        return "JOB_CANCELLED"
    if isinstance(error, ValueError) and str(error).isupper():
        return str(error)
    return "ENGINE_INTERNAL_ERROR"


def main() -> None:
    sidecar = Sidecar()
    for raw in sys.stdin.buffer:
        request_id = ""
        try:
            if len(raw) > MAX_FRAME:
                raise ValueError("FRAME_TOO_LARGE")
            frame = json.loads(raw.decode("utf-8"))
            request_id = str(frame.get("id", ""))
            if frame.get("v") != PROTOCOL_VERSION:
                raise ValueError("PROTOCOL_MISMATCH")
            result = sidecar.dispatch(str(frame["method"]), dict(frame.get("params", {})))
            emit({"v": 1, "id": request_id, "ok": True, "result": result})
        except Exception as error:  # protocol boundary intentionally catches all failures
            code = error_code(error)
            if code == "ENGINE_INTERNAL_ERROR":
                traceback.print_exc(file=sys.stderr)
            emit(
                {
                    "v": 1,
                    "id": request_id,
                    "ok": False,
                    "error": {"code": code, "message": safe_message(code)},
                }
            )


def safe_message(code: str) -> str:
    messages = {
        "DOCUMENT_INVALID_TYPE": "Tệp không phải DOCX hợp lệ.",
        "DOCUMENT_INVALID_PACKAGE": "Không thể đọc cấu trúc tệp Word.",
        "DOCUMENT_UNSAFE_ZIP_ENTRY": "Tệp Word chứa đường dẫn không an toàn.",
        "DOCUMENT_ARCHIVE_LIMIT": "Tệp Word vượt giới hạn an toàn.",
        "JOB_CANCELLED": "Đã dừng xử lý.",
        "PROTOCOL_MISMATCH": "Phiên bản engine không tương thích.",
        "FRAME_TOO_LARGE": "Yêu cầu vượt kích thước cho phép.",
        "METHOD_NOT_FOUND": "Lệnh không được hỗ trợ.",
        "INVALID_PARAMS": "Tham số không hợp lệ.",
        "CSV_INVALID_HEADER": "CSV phải có hai cột word,note.",
        "MODEL_PROVISIONING_OWNED_BY_HOST": "Model được quản lý bởi ứng dụng desktop.",
    }
    return messages.get(code, "Không thể hoàn tất yêu cầu.")


if __name__ == "__main__":
    main()
