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
from soatvan.checking import Preset, RuleConfig, RuleEngine
from soatvan.custom_rules import SqliteCustomRuleRepository
from soatvan.dictionary import SqliteDictionaryRepository
from soatvan.document import DocxPackage, InvalidDocument
from soatvan.models import ModelRegistry, runtime_available
from soatvan.workflow import ProcessDocument, ProcessRequest

MAX_FRAME = 1024 * 1024
EMIT_LOCK = threading.Lock()
PUBLIC_METHODS = frozenset(
    {
        "engine.hello",
        "document.inspect",
        "job.start",
        "job.cancel",
        "custom_rule.list",
        "custom_rule.upsert",
        "custom_rule.delete",
        "model.status",
        "model.download",
        "model.import",
        "model.cancel",
        "model.remove",
    }
)
PUBLIC_EVENTS = frozenset(
    {
        "job.progress",
        "job.completed",
        "job.no_findings",
        "job.failed",
        "model.progress",
        "model.state_changed",
    }
)


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
        configured_data = os.environ.get("SOATVAN_DATA_DIR")
        local_data = (
            Path(configured_data)
            if configured_data
            else Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
            / "SoatVan"
        )
        self.documents = DocxPackage()
        self.dictionary = SqliteDictionaryRepository(local_data / "dictionary.db")
        self.custom_rules = SqliteCustomRuleRepository(local_data / "preferences.db")
        self.models = ModelRegistry(local_data / "models")
        self.processor = ProcessDocument(self.documents, self.dictionary, RuleEngine(), self.models)
        self.jobs: dict[str, Token] = {}

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        handlers = {
            "engine.hello": self.hello,
            "document.inspect": self.inspect,
            "job.start": self.start_job,
            "job.cancel": self.cancel_job,
            "custom_rule.list": self.custom_rule_list,
            "custom_rule.upsert": self.custom_rule_upsert,
            "custom_rule.delete": self.custom_rule_delete,
            "model.status": self.model_status,
            "model.remove": self.model_remove,
        }
        if method in {"model.download", "model.import", "model.cancel"}:
            raise ValueError("MODEL_PROVISIONING_OWNED_BY_HOST")
        if method not in handlers:
            raise ValueError("METHOD_NOT_FOUND")
        return handlers[method](params)

    def hello(self, _: dict[str, Any]) -> dict[str, Any]:
        capabilities = ["rules", "custom_rules", "docx_annotations"]
        if runtime_available():
            capabilities.extend(("model_classifier", "model_full_review"))
        return {
            "protocol": PROTOCOL_VERSION,
            "engine_version": __version__,
            "capabilities": capabilities,
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
                _rule_config(params.get("rule_config")),
                _boolean_param(params, "use_model"),
                _custom_prompt(params.get("custom_prompt", "")),
                _ignored_words(params.get("ignored_words", [])),
                _boolean_param(params, "full_review"),
                _boolean_param(params, "include_rule_findings"),
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
            review_partial = bool(
                result.review and result.review.get("status") == "partial"
            )
            event = (
                "job.completed"
                if result.output_path or review_partial
                else "job.no_findings"
            )
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
                        "review": result.review,
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

    def custom_rule_list(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "entries": [asdict(entry) for entry in self.custom_rules.list()]
        }

    def custom_rule_upsert(self, params: dict[str, Any]) -> dict[str, Any]:
        return asdict(self.custom_rules.upsert(params["prompt"], params.get("id")))

    def custom_rule_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"deleted": self.custom_rules.delete(params["id"])}

    def model_status(self, params: dict[str, Any]) -> dict[str, Any]:
        activate = params.get("activate", True)
        if not isinstance(activate, bool):
            raise ValueError("INVALID_PARAMS")
        return self.models.status(activate=activate)

    def model_remove(self, _: dict[str, Any]) -> dict[str, Any]:
        self.models.deactivate()
        return {"deactivated": True}


def _rule_config(value: object) -> RuleConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("RULE_CONFIG_INVALID")
    return RuleConfig.from_dict(value)


def _boolean_param(params: dict[str, Any], name: str) -> bool:
    value = params.get(name, False)
    if not isinstance(value, bool):
        raise ValueError("INVALID_PARAMS")
    return value


def _custom_prompt(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("INVALID_PARAMS")
    if len(value) > 4200:
        raise ValueError("CUSTOM_PROMPT_TOO_LONG")
    return value


def _ignored_words(value: object) -> frozenset[str]:
    if not isinstance(value, list) or len(value) > 500:
        raise ValueError("SESSION_DICTIONARY_INVALID")
    words: set[str] = set()
    for item in value:
        if (
            not isinstance(item, str)
            or not item.strip()
            or len(item) > 120
            or any(character in item for character in "\r\n\x00")
        ):
            raise ValueError("SESSION_DICTIONARY_INVALID")
        words.add(item.strip())
    return frozenset(words)


def validate_request(frame: object) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(frame, dict) or set(frame) != {"v", "id", "method", "params"}:
        raise ValueError("INVALID_FRAME")
    request_id = frame["id"]
    method = frame["method"]
    params = frame["params"]
    if (
        not isinstance(request_id, str)
        or not request_id
        or not isinstance(method, str)
        or not method
        or not isinstance(params, dict)
    ):
        raise ValueError("INVALID_FRAME")
    if frame["v"] != PROTOCOL_VERSION:
        raise ValueError("PROTOCOL_MISMATCH")
    return request_id, method, params


def emit(frame: dict[str, Any]) -> None:
    with EMIT_LOCK:
        payload = (json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()


def error_code(error: Exception) -> str:
    if isinstance(error, KeyError):
        return "INVALID_PARAMS"
    if isinstance(error, InvalidDocument):
        return str(error)
    if isinstance(error, CancelledError):
        return "JOB_CANCELLED"
    if isinstance(error, (json.JSONDecodeError, UnicodeDecodeError)):
        return "INVALID_FRAME"
    if isinstance(error, ValueError) and str(error).isupper():
        return str(error)
    return "ENGINE_INTERNAL_ERROR"


def main() -> None:
    sidecar = Sidecar()
    while True:
        raw = sys.stdin.buffer.readline()
        if not raw:
            break
        request_id = ""
        try:
            if len(raw) > MAX_FRAME:
                raise ValueError("FRAME_TOO_LARGE")
            frame = json.loads(raw.decode("utf-8"))
            request_id, method, params = validate_request(frame)
            result = sidecar.dispatch(method, params)
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
        "DOCUMENT_FINDINGS_NOT_EXPORTABLE": "Không thể gắn các cảnh báo vào cấu trúc tài liệu này.",
        "MODEL_FULL_REVIEW_NOT_APPROVED": "Model này chưa được phê duyệt để rà soát toàn văn.",
        "DOCUMENT_INVALID_TYPE": "Tệp không phải DOCX hợp lệ.",
        "DOCUMENT_INVALID_PACKAGE": "Không thể đọc cấu trúc tệp Word.",
        "DOCUMENT_UNSAFE_ZIP_ENTRY": "Tệp Word chứa đường dẫn không an toàn.",
        "DOCUMENT_ARCHIVE_LIMIT": "Tệp Word vượt giới hạn an toàn.",
        "OUTPUT_SOURCE_CONFLICT": "Không thể ghi đè tệp nguồn.",
        "JOB_CANCELLED": "Đã dừng xử lý.",
        "PROTOCOL_MISMATCH": "Phiên bản engine không tương thích.",
        "FRAME_TOO_LARGE": "Yêu cầu vượt kích thước cho phép.",
        "INVALID_FRAME": "Khung giao tiếp không hợp lệ.",
        "METHOD_NOT_FOUND": "Lệnh không được hỗ trợ.",
        "INVALID_PARAMS": "Tham số không hợp lệ.",
        "CSV_INVALID_HEADER": "CSV phải có hai cột word,note.",
        "MODEL_PROVISIONING_OWNED_BY_HOST": "Model được quản lý bởi ứng dụng desktop.",
        "MODEL_CLASSIFIER_NOT_READY": "Model AI chưa sẵn sàng.",
        "MODEL_FULL_REVIEW_UNSUPPORTED": "Model AI không hỗ trợ rà soát toàn văn.",
        "MODEL_FULL_REVIEW_FAILED": "Model AI không rà soát được nội dung tài liệu.",
        "MODEL_REVIEW_CONTEXT_TOO_SMALL": "Cửa sổ ngữ cảnh của model quá nhỏ để rà soát toàn văn.",
        "CUSTOM_PROMPT_REQUIRES_MODEL": "Prompt riêng yêu cầu bật model AI.",
        "FULL_REVIEW_REQUIRES_MODEL": "Rà soát toàn văn yêu cầu bật model AI.",
        "INCLUDE_RULE_FINDINGS_REQUIRES_FULL_REVIEW": (
            "Quy tắc tự động chỉ có thể được bổ sung khi bật AI rà soát toàn văn."
        ),
        "CUSTOM_PROMPT_TOO_LONG": "Nội dung quy tắc riêng vượt giới hạn cho phép.",
        "RULE_CONFIG_INVALID": "Cấu hình quy tắc không hợp lệ.",
        "SESSION_DICTIONARY_INVALID": "Danh sách từ bỏ qua không hợp lệ.",
        "CUSTOM_RULE_INVALID_ID": "Mã quy tắc riêng không hợp lệ.",
        "CUSTOM_RULE_INVALID_PROMPT": "Nội dung quy tắc riêng không hợp lệ.",
        "CUSTOM_RULE_LIMIT_REACHED": "Các quy tắc riêng đã đạt giới hạn lưu trữ.",
        "MODEL_INFERENCE_TIMEOUT": "Model AI vượt quá thời gian xử lý cho phép.",
    }
    return messages.get(code, "Không thể hoàn tất yêu cầu.")


if __name__ == "__main__":
    main()
