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
from soatvan.custom_rules import (
    AiConfigEntry,
    SqliteAiConfigRepository,
    SqliteCustomRuleRepository,
)
from soatvan.custom_rules.seq2seq_config_repository import SqliteSeq2SeqConfigRepository
from soatvan.dictionary import SqliteDictionaryRepository
from soatvan.document import DocxPackage, InvalidDocument
from soatvan.models import (
    CloudAiReviewer,
    ModelRegistry,
    runtime_available,
    test_ai_connection,
)
from soatvan.workflow import ProcessDocument, ProcessRequest
from soatvan.workflow.ports import ContextClassifier

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
        "model.import",
        "model.cancel",
        "model.remove",
        "ai_config.get",
        "ai_config.update",
        "ai_config.set_active",
        "ai_config.test_connection",
    }
)
PUBLIC_EVENTS = frozenset(
    {
        "job.progress",
        "job.completed",
        "job.no_findings",
        "job.failed",
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


class DynamicClassifierProvider:
    def __init__(
        self,
        models: ModelRegistry,
        ai_config: SqliteAiConfigRepository,
    ) -> None:
        self._models = models
        self._ai_config = ai_config

    def classifier(self) -> ContextClassifier | None:
        active = self._ai_config.get_active_config()
        if active is not None and active.provider in {"openai", "gemini"}:
            if not active.api_key:
                return None
            return CloudAiReviewer(active)
        return self._models.classifier()

    def supports_full_review(self) -> bool:
        active = self._ai_config.get_active_config()
        if active is not None and active.provider in {"openai", "gemini"}:
            return bool(active.api_key)
        return self._models.supports_full_review()


class Sidecar:
    def __init__(self, local_data: Path | None = None) -> None:
        if local_data is None:
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
        self.ai_config = SqliteAiConfigRepository(local_data / "preferences.db")
        self.models = ModelRegistry(local_data / "models")
        self.classifiers = DynamicClassifierProvider(self.models, self.ai_config)
        self.seq2seq_config_repo = SqliteSeq2SeqConfigRepository(local_data / "preferences.db")
        _seq2seq_cfg = self.seq2seq_config_repo.get_config()
        from soatvan.workflow.seq2seq_provider import LocalSeq2SeqProvider
        self.seq2seq = LocalSeq2SeqProvider(model_dir=_seq2seq_cfg.model_dir if _seq2seq_cfg.is_configured else None)
        self.processor = ProcessDocument(
            self.documents, self.dictionary, RuleEngine(), self.classifiers, self.seq2seq
        )
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
            "ai_config.get": self.ai_config_get,
            "ai_config.update": self.ai_config_update,
            "ai_config.set_active": self.ai_config_set_active,
            "ai_config.test_connection": self.ai_config_test_connection,
            "seq2seq_config.get": self.seq2seq_config_get,
            "seq2seq_config.update": self.seq2seq_config_update,
        }
        if method in {"model.import", "model.cancel"}:
            raise ValueError("MODEL_PROVISIONING_OWNED_BY_HOST")
        if method not in handlers:
            raise ValueError("METHOD_NOT_FOUND")
        return handlers[method](params)

    def hello(self, _: dict[str, Any]) -> dict[str, Any]:
        capabilities = ["rules", "custom_rules", "docx_annotations", "ai_config"]
        active_ai = self.ai_config.get_active_config()
        if (
            active_ai is not None
            and active_ai.provider in {"openai", "gemini"}
            and bool(active_ai.api_key)
        ) or runtime_available():
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
        sys.stderr.write(
            f"[SoatVan-Sidecar] _run_job starting: use_model={params.get('use_model')}, "
            f"full_review={params.get('full_review')}, custom_prompt={params.get('custom_prompt')!r}\n"
        )
        sys.stderr.flush()
        try:
            _seq2seq_cfg = self.seq2seq_config_repo.get_config()
            request = ProcessRequest(
                source=Path(params["source_path"]),
                temporary_output=temporary_output,
                preset=Preset(params.get("preset", "standard")),
                rule_config=_rule_config(params.get("rule_config")),
                use_model=_boolean_param(params, "use_model"),
                use_seq2seq=(
                    self.seq2seq is not None
                    and self.seq2seq.is_ready()
                    and _seq2seq_cfg.is_enabled
                ),
                custom_prompt=_custom_prompt(params.get("custom_prompt", "")),
                ignored_words=_ignored_words(params.get("ignored_words", [])),
                full_review=_boolean_param(params, "full_review"),
                include_rule_findings=_boolean_param(params, "include_rule_findings"),
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
            sys.stderr.write(
                f"[SoatVan-Sidecar] _run_job finished successfully: finding_count={result.finding_count}, "
                f"output_path={result.output_path}, review={result.review}\n"
            )
            sys.stderr.flush()
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
            _log_dev_exception(f"job id={job_id}", error, code)
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
        return asdict(
            self.custom_rules.upsert(
                params["prompt"],
                params.get("id"),
                params.get("title"),
                params.get("is_default", False),
            )
        )

    def custom_rule_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"deleted": self.custom_rules.delete(params["id"])}

    def model_status(self, params: dict[str, Any]) -> dict[str, Any]:
        active_ai = self.ai_config.get_active_config()
        if active_ai is not None and active_ai.provider in {"openai", "gemini"}:
            if not active_ai.api_key:
                return {
                    "state": "installed",
                    "model_id": active_ai.model_name,
                    "version": active_ai.provider,
                    "code": "API_KEY_REQUIRED",
                }
            return {
                "state": "ready",
                "model_id": active_ai.model_name,
                "version": active_ai.provider,
                "trust": "release_signed",
                "release_approved": True,
                "capabilities": {
                    "candidate_filter": True,
                    "full_review": True,
                },
            }
        activate = params.get("activate", True)
        if not isinstance(activate, bool):
            raise ValueError("INVALID_PARAMS")
        return self.models.status(activate=activate)

    def model_remove(self, _: dict[str, Any]) -> dict[str, Any]:
        self.models.deactivate()
        return {"deactivated": True}

    def ai_config_get(self, _: dict[str, Any]) -> dict[str, Any]:
        active = self.ai_config.get_active_config()
        configs = self.ai_config.list_configs()
        return {
            "active_provider": active.provider if active else "local",
            "configs": [
                {
                    "provider": c.provider,
                    "api_key": c.api_key,
                    "masked_key": c.masked_key(),
                    "base_url": c.base_url,
                    "model_name": c.model_name,
                    "temperature": c.temperature,
                    "timeout_seconds": c.timeout_seconds,
                    "is_active": c.is_active,
                }
                for c in configs
            ],
        }

    def ai_config_update(self, params: dict[str, Any]) -> dict[str, Any]:
        provider = params.get("provider")
        if not isinstance(provider, str) or provider not in {"openai", "gemini"}:
            raise ValueError("INVALID_PARAMS")
        temperature = params.get("temperature", 0.0)
        if not isinstance(temperature, (int, float)):
            raise ValueError("INVALID_PARAMS")
        timeout_seconds = params.get("timeout_seconds", 60)
        if not isinstance(timeout_seconds, int):
            raise ValueError("INVALID_PARAMS")
        is_active = params.get("is_active", False)
        if not isinstance(is_active, bool):
            raise ValueError("INVALID_PARAMS")

        stored = self.ai_config.get_config(provider)
        api_key_param = params.get("api_key")
        if api_key_param is None:
            api_key = stored.api_key if stored else ""
        elif isinstance(api_key_param, str):
            api_key = api_key_param.strip()
        else:
            raise ValueError("INVALID_PARAMS")

        base_url_param = params.get("base_url")
        if base_url_param is None:
            base_url = (
                stored.base_url
                if stored and stored.base_url
                else (
                    "https://generativelanguage.googleapis.com/v1beta"
                    if provider == "gemini"
                    else "https://api.openai.com/v1"
                )
            )
        elif isinstance(base_url_param, str):
            base_url = base_url_param.strip() or (
                stored.base_url
                if stored and stored.base_url
                else (
                    "https://generativelanguage.googleapis.com/v1beta"
                    if provider == "gemini"
                    else "https://api.openai.com/v1"
                )
            )
        else:
            raise ValueError("INVALID_PARAMS")

        model_name_param = params.get("model_name")
        if model_name_param is None:
            model_name = (
                stored.model_name
                if stored and stored.model_name
                else ("gemini-2.5-flash" if provider == "gemini" else "gpt-4o-mini")
            )
        elif isinstance(model_name_param, str):
            model_name = model_name_param.strip() or (
                stored.model_name
                if stored and stored.model_name
                else ("gemini-2.5-flash" if provider == "gemini" else "gpt-4o-mini")
            )
        else:
            raise ValueError("INVALID_PARAMS")

        entry = self.ai_config.upsert_config(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            temperature=float(temperature),
            timeout_seconds=timeout_seconds,
            is_active=is_active,
        )
        return {
            "updated": True,
            "config": {
                "provider": entry.provider,
                "api_key": entry.api_key,
                "masked_key": entry.masked_key(),
                "base_url": entry.base_url,
                "model_name": entry.model_name,
                "temperature": entry.temperature,
                "timeout_seconds": entry.timeout_seconds,
                "is_active": entry.is_active,
            },
        }

    def ai_config_set_active(self, params: dict[str, Any]) -> dict[str, Any]:
        provider = params.get("provider")
        if not isinstance(provider, str) or provider not in {"local", "openai", "gemini"}:
            raise ValueError("INVALID_PARAMS")
        self.ai_config.set_active_provider(provider)
        return {"active_provider": provider}

    def ai_config_test_connection(self, params: dict[str, Any]) -> dict[str, Any]:
        provider = params.get("provider")
        if not isinstance(provider, str) or provider not in {"openai", "gemini"}:
            raise ValueError("INVALID_PARAMS")
        api_key = params.get("api_key")
        base_url = params.get("base_url")
        model_name = params.get("model_name")

        stored = self.ai_config.get_config(provider)
        if not isinstance(api_key, str) or not api_key:
            api_key = stored.api_key if stored else ""
        if not isinstance(base_url, str) or not base_url:
            base_url = (
                stored.base_url
                if stored
                else (
                    "https://generativelanguage.googleapis.com/v1beta"
                    if provider == "gemini"
                    else "https://api.openai.com/v1"
                )
            )
        if not isinstance(model_name, str) or not model_name:
            model_name = (
                stored.model_name
                if stored
                else ("gemini-2.5-flash" if provider == "gemini" else "gpt-4o-mini")
            )

        entry = AiConfigEntry(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
        )
        return test_ai_connection(entry)

    def seq2seq_config_get(self, _: dict[str, Any]) -> dict[str, Any]:
        cfg = self.seq2seq_config_repo.get_config()
        return {
            "model_dir": cfg.model_dir,
            "is_configured": cfg.is_configured,
            "is_valid": cfg.is_valid(),
            "is_enabled": cfg.is_enabled,
        }

    def seq2seq_config_update(self, params: dict[str, Any]) -> dict[str, Any]:
        model_dir = params.get("model_dir")
        is_enabled = params.get("is_enabled")
        if is_enabled is not None:
            is_enabled = bool(is_enabled)

        cfg = self.seq2seq_config_repo.set_config(
            model_dir=str(model_dir) if model_dir is not None else None,
            is_enabled=is_enabled,
        )
        if model_dir is not None:
            from soatvan.workflow.seq2seq_provider import LocalSeq2SeqProvider

            self.seq2seq = LocalSeq2SeqProvider(
                model_dir=cfg.model_dir if cfg.is_configured else None
            )
            self.processor._seq2seq = self.seq2seq
        return {
            "model_dir": cfg.model_dir,
            "is_configured": cfg.is_configured,
            "is_valid": cfg.is_valid(),
            "is_enabled": cfg.is_enabled,
        }


EngineSidecar = Sidecar


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


def _log_dev_exception(context: str, error: Exception, code: str) -> None:
    if os.environ.get("SOATVAN_DEV_LOG") != "1":
        return
    print(
        f"[engine-error] {context} failed code={code} "
        f"type={type(error).__name__}",
        file=sys.stderr,
        flush=True,
    )
    traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)


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
            _log_dev_exception(f"request id={request_id or '<unknown>'}", error, code)
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
        "CUSTOM_PROMPT_CONTEXT_EXCEEDED": (
            "Quy tắc riêng quá dài so với cửa sổ ngữ cảnh đang dùng."
        ),
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
        "CUSTOM_RULE_INVALID_TITLE": "Tiêu đề quy tắc riêng không hợp lệ.",
        "CUSTOM_RULE_INVALID_DEFAULT": "Cờ chọn sẵn của quy tắc riêng không hợp lệ.",
        "CUSTOM_RULE_LIMIT_REACHED": "Các quy tắc riêng đã đạt giới hạn lưu trữ.",
        "MODEL_INFERENCE_TIMEOUT": "Model AI vượt quá thời gian xử lý cho phép.",
        "API_KEY_REQUIRED": "API key không được để trống.",
        "API_KEY_INVALID": "API key không hợp lệ hoặc không có quyền truy cập.",
        "MODEL_NOT_FOUND": "Không tìm thấy model hoặc sai URL.",
        "CONNECTION_FAILED": "Không thể kết nối đến máy chủ AI.",
    }
    return messages.get(code, "Không thể hoàn tất yêu cầu.")


if __name__ == "__main__":
    main()
