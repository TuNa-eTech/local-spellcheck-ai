from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterable
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast

from soatvan.workflow.ports import (
    CancellationToken,
    ClassificationCandidate,
    ClassifierVerdict,
)


class ModelRuntimeUnavailable(RuntimeError):
    pass


class ModelLoadFailed(RuntimeError):
    pass


class ModelInferenceTimeout(ValueError):
    pass


class CompletionRuntime(Protocol):
    def create_chat_completion(self, **kwargs: Any) -> Any: ...


RuntimeFactory = Callable[[Path, int, int], CompletionRuntime]

VERDICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["candidate_id", "verdict", "confidence"],
                "properties": {
                    "candidate_id": {"type": "string"},
                    "verdict": {"enum": ["keep", "drop"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        }
    },
}


def runtime_available() -> bool:
    try:
        import_module("llama_cpp")
        return True
    except ImportError:
        return False


class _NativeLlamaRuntime:
    def __init__(self, runtime: Any, module: Any) -> None:
        self._runtime = runtime
        self._module = module
        self._abort_callback: Any = None

    def set_abort_predicate(self, predicate: Callable[[], bool]) -> None:
        self._abort_callback = self._module.ggml_abort_callback(
            lambda _data: bool(predicate())
        )
        self._module.llama_set_abort_callback(
            self._runtime.ctx, self._abort_callback, None
        )

    def create_chat_completion(self, **kwargs: Any) -> Any:
        return self._runtime.create_chat_completion(**kwargs)

    def reset_after_abort(self) -> None:
        self._runtime.reset()

    def close(self) -> None:
        self._runtime.close()


def _default_runtime_factory(model_path: Path, context_size: int, seed: int) -> CompletionRuntime:
    try:
        llama = import_module("llama_cpp")
    except ImportError as error:
        raise ModelRuntimeUnavailable("llama-cpp-python is not installed") from error
    try:
        runtime = llama.Llama(
            model_path=str(model_path),
            n_ctx=context_size,
            seed=seed,
            n_gpu_layers=0,
            verbose=False,
        )
        return cast(CompletionRuntime, _NativeLlamaRuntime(runtime, llama))
    except Exception as error:
        raise ModelLoadFailed("MODEL_LOAD_FAILED") from error


class LlamaCppClassifier:
    """Schema-constrained local classifier adapter.

    The workflow only sees its ContextClassifier port. Model/runtime details and
    untrusted JSON stay at this outer boundary.
    """

    def __init__(
        self,
        model_path: Path,
        manifest: dict[str, Any],
        runtime_factory: RuntimeFactory = _default_runtime_factory,
    ) -> None:
        self._version = f"model-{manifest['model_id']}@{manifest['version']}"
        self._minimum_confidence = float(manifest.get("minimum_confidence", 0.5))
        self._batch_size = int(manifest.get("batch_size", 8))
        self._max_tokens = int(manifest.get("max_tokens", 512))
        self._timeout_seconds = int(manifest.get("timeout_seconds", 120))
        self._seed = int(manifest.get("seed", 42))
        context_size = int(manifest.get("context_size", 2048))
        self._runtime = runtime_factory(model_path, context_size, self._seed)
        self._lock = threading.Lock()

    @property
    def version(self) -> str:
        return self._version

    @property
    def minimum_confidence(self) -> float:
        return self._minimum_confidence

    def close(self) -> None:
        with self._lock:
            close = getattr(self._runtime, "close", None)
            if callable(close):
                close()

    def classify(
        self,
        candidates: tuple[ClassificationCandidate, ...],
        custom_prompt: str,
        cancellation: CancellationToken,
    ) -> tuple[ClassifierVerdict, ...]:
        verdicts: list[ClassifierVerdict] = []
        deadline = time.monotonic() + self._timeout_seconds
        abort_reason: list[Exception] = []

        def should_abort() -> bool:
            try:
                cancellation.raise_if_cancelled()
            except Exception as error:
                if not abort_reason:
                    abort_reason.append(error)
                return True
            if time.monotonic() > deadline:
                if not abort_reason:
                    abort_reason.append(ModelInferenceTimeout("MODEL_INFERENCE_TIMEOUT"))
                return True
            return False

        with self._lock:
            set_abort = getattr(self._runtime, "set_abort_predicate", None)
            if callable(set_abort):
                set_abort(should_abort)
            for offset in range(0, len(candidates), self._batch_size):
                cancellation.raise_if_cancelled()
                batch = candidates[offset : offset + self._batch_size]
                try:
                    completion = self._runtime.create_chat_completion(
                    messages=[
                    {
                        "role": "system",
                        "content": (
                            "Bạn là bộ phân loại lỗi tiếng Việt chạy cục bộ. "
                            "Chỉ đánh giá candidate đã cho. Trả JSON duy nhất dạng "
                            '{"verdicts":[{"candidate_id":"...","verdict":"keep|drop",'
                            '"confidence":0.0}]}. Không thêm candidate và không sửa văn bản.'
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "custom_rule": custom_prompt,
                                "candidates": [
                                    {
                                        "candidate_id": item.candidate_id,
                                        "paragraph_id": item.paragraph_id,
                                        "source_text": item.source_text,
                                        "suggestion": item.suggestion,
                                        "reason_code": item.reason_code,
                                        "occurrence_index": item.occurrence_index,
                                        "context": item.context,
                                    }
                                    for item in batch
                                ],
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                    ],
                    temperature=0,
                    seed=self._seed,
                    max_tokens=self._max_tokens,
                    stream=True,
                    response_format={"type": "json_object", "schema": VERDICT_SCHEMA},
                    )
                    raw = _collect_stream(completion, cancellation, deadline)
                except Exception as error:
                    reset = getattr(self._runtime, "reset_after_abort", None)
                    if callable(reset):
                        reset()
                    if abort_reason:
                        raise abort_reason[0] from error
                    raise
                if abort_reason:
                    reset = getattr(self._runtime, "reset_after_abort", None)
                    if callable(reset):
                        reset()
                    raise abort_reason[0]
                cancellation.raise_if_cancelled()
                verdicts.extend(
                    _parse_verdicts(raw, {candidate.candidate_id for candidate in batch})
                )
        return tuple(verdicts)


def _collect_stream(
    completion: object, cancellation: CancellationToken, deadline: float
) -> dict[str, Any]:
    if isinstance(completion, dict):
        if time.monotonic() > deadline:
            raise ModelInferenceTimeout("MODEL_INFERENCE_TIMEOUT")
        return completion
    content: list[str] = []
    try:
        iterator = iter(cast(Iterable[Any], completion))
    except TypeError:
        return {}
    for chunk in iterator:
        cancellation.raise_if_cancelled()
        if time.monotonic() > deadline:
            raise ModelInferenceTimeout("MODEL_INFERENCE_TIMEOUT")
        try:
            value = chunk["choices"][0]["delta"].get("content")
        except (KeyError, IndexError, TypeError):
            continue
        if isinstance(value, str):
            content.append(value)
    return {"choices": [{"message": {"content": "".join(content)}}]}


def _parse_verdicts(response: dict[str, Any], accepted_ids: set[str]) -> list[ClassifierVerdict]:
    try:
        content = response["choices"][0]["message"]["content"]
        payload = json.loads(content)
        items = payload["verdicts"]
        if not isinstance(items, list):
            return []
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return []
    parsed: list[ClassifierVerdict] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"candidate_id", "verdict", "confidence"}:
            continue
        candidate_id = item["candidate_id"]
        verdict = item["verdict"]
        confidence = item["confidence"]
        if (
            not isinstance(candidate_id, str)
            or candidate_id not in accepted_ids
            or candidate_id in seen
            or verdict not in {"keep", "drop"}
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
        ):
            continue
        seen.add(candidate_id)
        parsed.append(ClassifierVerdict(candidate_id, verdict, float(confidence)))
    return parsed
