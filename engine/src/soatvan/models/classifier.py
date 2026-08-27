from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterable
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast

from soatvan.checking.domain import Block
from soatvan.workflow.ports import (
    CancellationToken,
    ClassificationCandidate,
    ClassifierVerdict,
    DiscoveryProposal,
    FullReviewResult,
    ReviewCandidate,
)

from .review import (
    LLM_ONLY_REVIEW_SCHEMA,
    LLM_ONLY_REVIEW_SYSTEM_PROMPT,
    REVIEW_SCHEMA,
    REVIEW_SYSTEM_PROMPT,
    ReviewChunk,
    parse_review_content,
    plan_review_chunks,
    split_llm_only_chunk,
)

LLM_ONLY_MAX_TOKENS = 768
LLM_ONLY_MAX_SPLIT_DEPTH = 2


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

    def count_tokens(self, value: str) -> int:
        return len(self._runtime.tokenize(value.encode("utf-8"), add_bos=False, special=False))

    def reset_after_abort(self) -> None:
        self._runtime.reset()

    def close(self) -> None:
        self._runtime.close()


def _default_runtime_factory(model_path: Path, context_size: int, seed: int) -> CompletionRuntime:
    try:
        llama = import_module("llama_cpp")
    except ImportError as error:
        raise ModelRuntimeUnavailable("llama-cpp-python is not installed") from error
    gpu_layers = _preferred_gpu_layers(llama)
    try:
        runtime = _create_llama_runtime(llama, model_path, context_size, seed, gpu_layers)
        return cast(CompletionRuntime, _NativeLlamaRuntime(runtime, llama))
    except Exception as error:
        if gpu_layers != 0:
            try:
                runtime = _create_llama_runtime(llama, model_path, context_size, seed, 0)
                return cast(CompletionRuntime, _NativeLlamaRuntime(runtime, llama))
            except Exception:
                pass
        raise ModelLoadFailed("MODEL_LOAD_FAILED") from error


def _preferred_gpu_layers(module: Any) -> int:
    supports_offload = getattr(module, "llama_supports_gpu_offload", None)
    if not callable(supports_offload):
        return 0
    try:
        return -1 if supports_offload() else 0
    except Exception:
        return 0


def _create_llama_runtime(
    module: Any, model_path: Path, context_size: int, seed: int, gpu_layers: int
) -> Any:
    return module.Llama(
        model_path=str(model_path),
        n_ctx=context_size,
        seed=seed,
        n_gpu_layers=gpu_layers,
        verbose=False,
    )


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
        self._review_candidate_limit = max(
            1, min(self._batch_size, self._max_tokens // 80)
        )
        self._timeout_seconds = int(manifest.get("timeout_seconds", 300))
        self._seed = int(manifest.get("seed", 42))
        self._context_size = int(manifest.get("context_size", 2048))
        available_review_tokens = self._context_size - self._max_tokens - 256
        if available_review_tokens < 64:
            raise ValueError("MODEL_REVIEW_CONTEXT_TOO_SMALL")
        requested_review_tokens = int(manifest.get("review_chunk_tokens", 1200))
        if "review_chunk_tokens" in manifest and requested_review_tokens > available_review_tokens:
            raise ValueError("MODEL_REVIEW_CONTEXT_TOO_SMALL")
        self._review_chunk_tokens = min(requested_review_tokens, available_review_tokens)
        self._llm_only_max_tokens = max(
            32,
            min(
                LLM_ONLY_MAX_TOKENS,
                self._context_size - self._review_chunk_tokens - 256,
            ),
        )
        self._runtime = runtime_factory(model_path, self._context_size, self._seed)
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

    def review(
        self,
        blocks: tuple[Block, ...],
        candidates: tuple[ReviewCandidate, ...],
        custom_prompt: str,
        cancellation: CancellationToken,
        progress: Callable[[int, int], None] | None = None,
    ) -> FullReviewResult:
        verdicts: list[ClassifierVerdict] = []
        discoveries: list[DiscoveryProposal] = []
        failed_chunks: list[str] = []
        failed_blocks: set[str] = set()
        reviewed_chunks = 0
        successful_attempts = 0
        failure_counts = {"timeout": 0, "invalid_output": 0, "inference_error": 0}
        retried_chunks = 0
        recovered_chunks = 0
        with self._lock:
            chunks = plan_review_chunks(
                blocks,
                candidates,
                custom_prompt,
                self._review_chunk_tokens,
                self._count_tokens,
                self._review_candidate_limit,
                cancellation.raise_if_cancelled,
            )
            if not chunks:
                return FullReviewResult((), (), 0, 0)
            for processed_chunks, chunk in enumerate(chunks, start=1):
                cancellation.raise_if_cancelled()
                (
                    chunk_verdicts,
                    chunk_discoveries,
                    chunk_failures,
                    chunk_retries,
                    chunk_successes,
                ) = self._review_chunk_with_retries(chunk, cancellation)
                verdicts.extend(chunk_verdicts)
                discoveries.extend(chunk_discoveries)
                retried_chunks += chunk_retries
                successful_attempts += chunk_successes
                if not chunk_failures:
                    reviewed_chunks += 1
                    if chunk_retries:
                        recovered_chunks += 1
                else:
                    failed_chunks.append(chunk.chunk_id)
                    for _failure, block_ids in chunk_failures:
                        failed_blocks.update(block_ids)
                    failure_counts[chunk_failures[0][0]] += 1
                if progress:
                    progress(processed_chunks, len(chunks))
        if chunks and successful_attempts == 0:
            raise ValueError("MODEL_FULL_REVIEW_FAILED")
        return FullReviewResult(
            tuple(verdicts),
            tuple(discoveries),
            len(chunks),
            reviewed_chunks,
            tuple(failed_chunks),
            tuple(sorted(failed_blocks)),
            failure_counts["timeout"],
            failure_counts["invalid_output"],
            failure_counts["inference_error"],
            retried_chunks,
            recovered_chunks,
        )

    def _review_chunk_with_retries(
        self,
        chunk: ReviewChunk,
        cancellation: CancellationToken,
        depth: int = 0,
    ) -> tuple[
        list[ClassifierVerdict],
        list[DiscoveryProposal],
        list[tuple[str, frozenset[str]]],
        int,
        int,
    ]:
        parsed, failure = self._review_chunk(chunk, cancellation)
        if parsed is not None:
            parsed_verdicts, parsed_discoveries = parsed
            return list(parsed_verdicts), list(parsed_discoveries), [], 0, 1
        if depth >= LLM_ONLY_MAX_SPLIT_DEPTH:
            return [], [], [(failure or "inference_error", chunk.target_block_ids)], 0, 0
        retries = split_llm_only_chunk(chunk)
        if not retries:
            return [], [], [(failure or "inference_error", chunk.target_block_ids)], 0, 0

        verdicts: list[ClassifierVerdict] = []
        discoveries: list[DiscoveryProposal] = []
        failures: list[tuple[str, frozenset[str]]] = []
        retry_count = 1
        successful_attempts = 0
        for retry in retries:
            (
                retry_verdicts,
                retry_discoveries,
                retry_failures,
                nested_retries,
                retry_successes,
            ) = self._review_chunk_with_retries(retry, cancellation, depth + 1)
            verdicts.extend(retry_verdicts)
            discoveries.extend(retry_discoveries)
            failures.extend(retry_failures)
            retry_count += nested_retries
            successful_attempts += retry_successes
        return verdicts, discoveries, failures, retry_count, successful_attempts

    def _review_chunk(
        self, chunk: ReviewChunk, cancellation: CancellationToken
    ) -> tuple[
        tuple[tuple[ClassifierVerdict, ...], tuple[DiscoveryProposal, ...]] | None,
        str | None,
    ]:
        deadline = time.monotonic() + self._timeout_seconds
        abort_reason: list[Exception] = []
        should_abort = _abort_predicate(cancellation, deadline, abort_reason)
        set_abort = getattr(self._runtime, "set_abort_predicate", None)
        if callable(set_abort):
            set_abort(should_abort)
        llm_only = not chunk.candidates
        try:
            completion = self._runtime.create_chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": LLM_ONLY_REVIEW_SYSTEM_PROMPT
                        if llm_only
                        else REVIEW_SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            chunk.payload(), ensure_ascii=False, separators=(",", ":")
                        ),
                    },
                ],
                temperature=0,
                seed=self._seed,
                max_tokens=self._llm_only_max_tokens if llm_only else self._max_tokens,
                stream=True,
                response_format={
                    "type": "json_object",
                    "schema": LLM_ONLY_REVIEW_SCHEMA if llm_only else REVIEW_SCHEMA,
                },
            )
            raw = _collect_stream(completion, cancellation, deadline)
        except Exception as error:
            reset = getattr(self._runtime, "reset_after_abort", None)
            if callable(reset):
                reset()
            try:
                cancellation.raise_if_cancelled()
            except Exception as cancellation_error:
                raise cancellation_error from error
            if abort_reason and not isinstance(abort_reason[0], ModelInferenceTimeout):
                raise abort_reason[0] from error
            timed_out = isinstance(error, ModelInferenceTimeout) or (
                abort_reason and isinstance(abort_reason[0], ModelInferenceTimeout)
            )
            return None, "timeout" if timed_out else "inference_error"
        if abort_reason:
            reset = getattr(self._runtime, "reset_after_abort", None)
            if callable(reset):
                reset()
            if not isinstance(abort_reason[0], ModelInferenceTimeout):
                raise abort_reason[0]
            return None, "timeout"
        parsed = parse_review_content(_response_content(raw), chunk)
        return (parsed, None) if parsed is not None else (None, "invalid_output")

    def _count_tokens(self, value: str) -> int:
        counter = getattr(self._runtime, "count_tokens", None)
        if callable(counter):
            try:
                count = counter(value)
                if isinstance(count, int) and count >= 0:
                    return count
            except (TypeError, ValueError):
                pass
        return max(1, (len(value.encode("utf-8")) + 2) // 3)


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


def _response_content(response: dict[str, Any]) -> str:
    try:
        value = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    return value if isinstance(value, str) else ""


def _abort_predicate(
    cancellation: CancellationToken, deadline: float, abort_reason: list[Exception]
) -> Callable[[], bool]:
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

    return should_abort


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
    # A batch response is only authoritative when it covers every candidate.
    # The workflow treats an empty result conservatively and keeps the original
    # rule findings instead of letting malformed or truncated model output hide
    # them.
    return parsed if seen == accepted_ids else []
