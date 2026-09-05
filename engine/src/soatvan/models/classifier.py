from __future__ import annotations

import json
import sys
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
    LIGHTWEIGHT_REVIEW_SCHEMA,
    LLM_ONLY_REVIEW_SCHEMA,
    REVIEW_SCHEMA,
    ReviewChunk,
    lightweight_review_messages,
    parse_lightweight_content,
    parse_review_content,
    plan_review_chunks,
    review_messages,
    split_llm_only_chunk,
)
from .review_budget import MIN_REVIEW_DOCUMENT_TOKENS, ReviewBudget

LLM_ONLY_2K_MAX_TOKENS = 768
LLM_ONLY_4K_MAX_TOKENS = 2048
LLM_ONLY_MAX_SPLIT_DEPTH = 2
REVIEW_SAFETY_TOKENS = 256
CHAT_FALLBACK_OVERHEAD_TOKENS = 32


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


def classification_messages(
    candidates: tuple[ClassificationCandidate, ...], custom_prompt: str
) -> list[dict[str, str]]:
    return [
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
                        for item in candidates
                    ],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    ]


def _attempt_activity(
    progress: Callable[[int, int], None] | None, processed: int, total: int
) -> Callable[[], None] | None:
    if progress is None:
        return None

    def activity() -> None:
        progress(processed, total)

    return activity


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
        self._chat_formatter = _metadata_chat_formatter(runtime, module)

    def set_abort_predicate(self, predicate: Callable[[], bool]) -> None:
        self._abort_callback = self._module.ggml_abort_callback(lambda _data: bool(predicate()))
        self._module.llama_set_abort_callback(self._runtime.ctx, self._abort_callback, None)

    def create_chat_completion(self, **kwargs: Any) -> Any:
        return self._runtime.create_chat_completion(**kwargs)

    def count_tokens(self, value: str) -> int:
        return len(self._runtime.tokenize(value.encode("utf-8"), add_bos=False, special=False))

    def count_chat_tokens(self, messages: list[dict[str, str]]) -> int:
        if self._chat_formatter is not None:
            try:
                formatted = self._chat_formatter(messages=messages)
                return len(
                    self._runtime.tokenize(
                        formatted.prompt.encode("utf-8"),
                        add_bos=not formatted.added_special,
                        special=True,
                    )
                )
            except (AttributeError, TypeError, ValueError):
                pass
        return (
            sum(self.count_tokens(message["content"]) for message in messages)
            + CHAT_FALLBACK_OVERHEAD_TOKENS
        )

    def reset_after_abort(self) -> None:
        self._runtime.reset()

    def close(self) -> None:
        self._runtime.close()


def _metadata_chat_formatter(runtime: Any, module: Any) -> Any | None:
    if getattr(runtime, "chat_handler", None) is not None:
        return None
    metadata = getattr(runtime, "metadata", None)
    chat_format = getattr(runtime, "chat_format", None)
    if not isinstance(metadata, dict) or not isinstance(chat_format, str):
        return None
    template: object | None = None
    if chat_format == "chat_template.default":
        template = metadata.get("tokenizer.chat_template")
    elif chat_format.startswith("chat_template."):
        template = metadata.get(f"tokenizer.{chat_format}")
    if not isinstance(template, str) or not template:
        return None
    try:
        eos_token_id = runtime.token_eos()
        bos_token_id = runtime.token_bos()
        eos_token = runtime._model.token_get_text(eos_token_id) if eos_token_id != -1 else ""
        bos_token = runtime._model.token_get_text(bos_token_id) if bos_token_id != -1 else ""
        return module.llama_chat_format.Jinja2ChatFormatter(
            template=template,
            eos_token=eos_token,
            bos_token=bos_token,
            stop_token_ids=[eos_token_id],
        )
    except (AttributeError, TypeError, ValueError):
        return None


def _default_runtime_factory(model_path: Path, context_size: int, seed: int) -> CompletionRuntime:
    import sys

    print(f"[soatvan-engine] _default_runtime_factory: model_path={model_path}, context_size={context_size}, seed={seed}", file=sys.stderr)
    try:
        llama = import_module("llama_cpp")
        print(f"[soatvan-engine] _default_runtime_factory: llama_cpp imported OK", file=sys.stderr)
    except ImportError as error:
        print(f"[soatvan-engine] _default_runtime_factory: llama_cpp NOT installed: {error}", file=sys.stderr)
        raise ModelRuntimeUnavailable("llama-cpp-python is not installed") from error
    gpu_layers = _preferred_gpu_layers(llama)
    print(f"[soatvan-engine] _default_runtime_factory: gpu_layers={gpu_layers}", file=sys.stderr)
    try:
        print(f"[soatvan-engine] _default_runtime_factory: loading model (gpu_layers={gpu_layers})...", file=sys.stderr)
        runtime = _create_llama_runtime(llama, model_path, context_size, seed, gpu_layers)
        print(f"[soatvan-engine] _default_runtime_factory: model loaded OK", file=sys.stderr)
        return cast(CompletionRuntime, _NativeLlamaRuntime(runtime, llama))
    except Exception as error:
        print(f"[soatvan-engine] _default_runtime_factory: FAILED with gpu_layers={gpu_layers}: {type(error).__name__}: {error}", file=sys.stderr)
        if gpu_layers != 0:
            try:
                print(f"[soatvan-engine] _default_runtime_factory: retrying with gpu_layers=0...", file=sys.stderr)
                runtime = _create_llama_runtime(llama, model_path, context_size, seed, 0)
                print(f"[soatvan-engine] _default_runtime_factory: model loaded OK (cpu fallback)", file=sys.stderr)
                return cast(CompletionRuntime, _NativeLlamaRuntime(runtime, llama))
            except Exception as fallback_error:
                print(f"[soatvan-engine] _default_runtime_factory: CPU fallback also FAILED: {type(fallback_error).__name__}: {fallback_error}", file=sys.stderr)
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
        self._lightweight_mode = manifest.get("review_mode") == "lightweight"
        self._version = f"model-{manifest['model_id']}@{manifest['version']}"
        self._minimum_confidence = float(manifest.get("minimum_confidence", 0.5))
        self._batch_size = int(manifest.get("batch_size", 8))
        self._filter_output_tokens = int(manifest.get("max_tokens", 512))
        self._review_candidate_limit = max(
            1, min(self._batch_size, self._filter_output_tokens // 80)
        )
        self._timeout_seconds = int(manifest.get("timeout_seconds", 300))
        self._seed = int(manifest.get("seed", 42))
        self._context_size = int(manifest.get("context_size", 2048))
        review_output_limit = (
            LLM_ONLY_4K_MAX_TOKENS if self._context_size >= 4096 else LLM_ONLY_2K_MAX_TOKENS
        )
        self._review_output_tokens = max(
            32,
            min(
                review_output_limit,
                self._context_size - REVIEW_SAFETY_TOKENS - MIN_REVIEW_DOCUMENT_TOKENS,
            ),
        )
        # Lightweight mode: error list output is much smaller than full JSON,
        # so cap output tokens to free more space for document text input.
        if self._lightweight_mode:
            self._review_output_tokens = min(
                self._review_output_tokens,
                max(256, self._context_size // 4),
            )
        response_tokens = max(
            self._filter_output_tokens,
            self._review_output_tokens,
        )
        configured_doc_tokens = int(manifest.get("review_chunk_tokens", 1200))
        # Lightweight mode: the prompt is much shorter, so we can fit more
        # document text per chunk.  Use input_tokens as the effective ceiling
        # instead of the manifest cap that was tuned for the heavy JSON format.
        if self._lightweight_mode:
            lightweight_input = (
                self._context_size - response_tokens - REVIEW_SAFETY_TOKENS
            )
            configured_doc_tokens = max(configured_doc_tokens, lightweight_input)
        self._review_budget = ReviewBudget(
            context_tokens=self._context_size,
            response_tokens=response_tokens,
            safety_tokens=REVIEW_SAFETY_TOKENS,
            document_tokens=configured_doc_tokens,
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
                messages = classification_messages(batch, custom_prompt)
                self._ensure_classification_request_fits(batch, custom_prompt, messages)
                try:
                    completion = self._runtime.create_chat_completion(
                        messages=messages,
                        temperature=0,
                        seed=self._seed,
                        max_tokens=self._filter_output_tokens,
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
            # Lightweight mode: candidates are irrelevant (no verdicts),
            # so skip them to avoid the candidate-limit fragmenting chunks.
            plan_candidates = () if self._lightweight_mode else candidates
            chunks = plan_review_chunks(
                blocks,
                plan_candidates,
                custom_prompt,
                self._review_budget,
                self._count_tokens,
                self._count_review_request_tokens,
                self._review_candidate_limit,
                cancellation.raise_if_cancelled,
            )
            if not chunks:
                return FullReviewResult((), (), 0, 0)
            total_chunks = len(chunks)
            if self._lightweight_mode:
                _b = self._review_budget
                from .review import ReviewSegment as _RS
                _empty = _RS("diag@0:0", "diag", 0, 0, "", "paragraph")
                _probe = ReviewChunk("diag", (_empty,), (), (), custom_prompt)
                _fixed = self._count_review_request_tokens(_probe)
                _doc_limit = _b.document_limit(_fixed)
                _total_doc = sum(
                    self._count_tokens(seg.text)
                    for c in chunks for seg in c.targets
                )
                sys.stderr.write(
                    f"[SoatVan-Diag] lightweight_mode=True "
                    f"output_tokens={self._review_output_tokens} "
                    f"input_tokens={_b.input_tokens} "
                    f"doc_limit_per_chunk={_doc_limit} "
                    f"total_doc_tokens={_total_doc} "
                    f"avg_doc_per_chunk={_total_doc // max(1, total_chunks)} "
                    f"total_chunks={total_chunks} "
                    f"prompt_overhead={_fixed}\n"
                )
                sys.stderr.flush()
            for processed_chunks, chunk in enumerate(chunks, start=1):
                cancellation.raise_if_cancelled()
                (
                    chunk_verdicts,
                    chunk_discoveries,
                    chunk_failures,
                    chunk_retries,
                    chunk_successes,
                ) = self._review_chunk_with_retries(
                    chunk,
                    cancellation,
                    _attempt_activity(progress, processed_chunks - 1, total_chunks),
                )
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
                    progress(processed_chunks, total_chunks)
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
        activity: Callable[[], None] | None = None,
        depth: int = 0,
    ) -> tuple[
        list[ClassifierVerdict],
        list[DiscoveryProposal],
        list[tuple[str, frozenset[str]]],
        int,
        int,
    ]:
        if activity:
            activity()
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
            ) = self._review_chunk_with_retries(retry, cancellation, activity, depth + 1)
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
        use_lightweight = self._lightweight_mode
        if use_lightweight:
            messages = lightweight_review_messages(chunk)
            schema = LIGHTWEIGHT_REVIEW_SCHEMA
        else:
            messages = review_messages(chunk)
            schema = LLM_ONLY_REVIEW_SCHEMA if llm_only else REVIEW_SCHEMA
        try:
            completion = self._runtime.create_chat_completion(
                messages=messages,
                temperature=0,
                seed=self._seed,
                max_tokens=self._review_output_tokens,
                stream=True,
                response_format={
                    "type": "json_object",
                    "schema": schema,
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
        if use_lightweight:
            parsed = parse_lightweight_content(_response_content(raw), chunk)
        else:
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

    def _count_chat_tokens(self, messages: list[dict[str, str]]) -> int:
        counter = getattr(self._runtime, "count_chat_tokens", None)
        if callable(counter):
            try:
                count = counter(messages)
                if not isinstance(count, bool) and isinstance(count, int) and count >= 0:
                    return int(count)
            except (TypeError, ValueError, RuntimeError):
                pass
        return (
            sum(self._count_tokens(message["content"]) for message in messages)
            + CHAT_FALLBACK_OVERHEAD_TOKENS
        )

    def _count_review_request_tokens(self, chunk: ReviewChunk) -> int:
        if self._lightweight_mode:
            return self._count_chat_tokens(lightweight_review_messages(chunk))
        return self._count_chat_tokens(review_messages(chunk))

    def _ensure_classification_request_fits(
        self,
        batch: tuple[ClassificationCandidate, ...],
        custom_prompt: str,
        messages: list[dict[str, str]],
    ) -> None:
        input_tokens = self._context_size - self._filter_output_tokens - REVIEW_SAFETY_TOKENS
        if self._count_chat_tokens(messages) <= input_tokens:
            return
        if (
            custom_prompt
            and self._count_chat_tokens(classification_messages(batch, "")) <= input_tokens
        ):
            raise ValueError("CUSTOM_PROMPT_CONTEXT_EXCEEDED")
        raise ValueError("MODEL_REVIEW_CONTEXT_TOO_SMALL")


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
