"""Cloud AI reviewer — thin orchestrator.

Builds review chunks, delegates HTTP calls to ``llm_transport``, delegates
response parsing to ``response_parser``, and aggregates the results.

Includes retry-with-split logic: when a chunk fails (timeout, HTTP 5xx,
or invalid JSON), the orchestrator splits it in half and retries each sub-chunk
independently, up to ``_MAX_SPLIT_DEPTH`` levels deep.
"""

from __future__ import annotations

import json
import socket
import sys
import time
import urllib.error
from collections.abc import Callable

from soatvan.checking.domain import Block
from soatvan.custom_rules.ai_config_repository import AiConfigEntry
from soatvan.models.llm_transport import call_llm
from soatvan.models.llm_transport import test_connection as test_ai_connection
from soatvan.models.response_parser import parse_llm_response
from soatvan.models.review import (
    LLM_ONLY_REVIEW_SYSTEM_PROMPT,
    REVIEW_SYSTEM_PROMPT,
)
from soatvan.workflow.ports import (
    CancellationToken,
    ClassificationCandidate,
    ClassifierVerdict,
    DiscoveryProposal,
    FullReviewResult,
    ReviewCandidate,
)

__all__ = [
    "CloudAiError",
    "CloudAiReviewer",
    "test_ai_connection",
]

_MAX_SPLIT_DEPTH = 2
_RETRY_BACKOFF_SECONDS = 2.0


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

def _classify_error(exc: Exception) -> str:
    """Return a failure category: ``timeout``, ``invalid_output``, or
    ``inference_error``."""
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, urllib.error.URLError):
        cause = exc.reason
        if isinstance(cause, (socket.timeout, TimeoutError)):
            return "timeout"
        return "inference_error"
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code >= 500:
            return "inference_error"
        # 4xx = client error (bad key, bad model) — not retryable
        return "inference_error"
    if isinstance(exc, (json.JSONDecodeError, KeyError, ValueError)):
        return "invalid_output"
    return "inference_error"


def _is_retryable(failure: str, exc: Exception) -> bool:
    """Whether the failure category warrants a retry."""
    if failure in ("timeout", "invalid_output"):
        return True
    # HTTP 5xx → retryable; 4xx → not
    return isinstance(exc, urllib.error.HTTPError) and exc.code >= 500


class CloudAiError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CloudAiReviewer:
    """Orchestrates chunk-based LLM review of Vietnamese documents."""

    def __init__(self, config: AiConfigEntry) -> None:
        self._config = config

    @property
    def version(self) -> str:
        return f"{self._config.provider}:{self._config.model_name}"

    @property
    def minimum_confidence(self) -> float:
        return 0.55

    # ------------------------------------------------------------------
    # Candidate classification (pass-through for cloud — keeps all)
    # ------------------------------------------------------------------

    def classify(
        self,
        candidates: tuple[ClassificationCandidate, ...],
        custom_prompt: str,
        cancellation: CancellationToken,
    ) -> tuple[ClassifierVerdict, ...]:
        cancellation.raise_if_cancelled()
        if not candidates:
            return ()
        return tuple(
            ClassifierVerdict(candidate.candidate_id, "keep", 0.95)
            for candidate in candidates
        )

    # ------------------------------------------------------------------
    # Full-text review
    # ------------------------------------------------------------------

    def review(
        self,
        blocks: tuple[Block, ...],
        candidates: tuple[ReviewCandidate, ...],
        custom_prompt: str,
        cancellation: CancellationToken,
        progress: Callable[[int, int], None] | None = None,
    ) -> FullReviewResult:
        cancellation.raise_if_cancelled()
        if not blocks:
            return FullReviewResult(
                verdicts=(),
                discoveries=(),
                total_chunks=0,
                reviewed_chunks=0,
            )

        chunks = self._build_chunks(blocks, candidates)
        total_chunks = len(chunks)
        reviewed_chunks = 0
        successful_attempts = 0
        all_verdicts: list[ClassifierVerdict] = []
        all_discoveries: list[DiscoveryProposal] = []
        failed_chunk_ids: list[str] = []
        failed_block_ids: set[str] = set()
        failure_counts = {"timeout": 0, "invalid_output": 0, "inference_error": 0}
        retried_chunks = 0
        recovered_chunks = 0

        system_prompt = (
            LLM_ONLY_REVIEW_SYSTEM_PROMPT
            if not candidates
            else REVIEW_SYSTEM_PROMPT
        )
        if custom_prompt:
            system_prompt += f"\n\nQuy tắc riêng:\n{custom_prompt}"

        for idx, (chunk_id, chunk_blocks, chunk_candidates) in enumerate(chunks):
            cancellation.raise_if_cancelled()
            sys.stderr.write(
                f"[SoatVan-CloudAI] Reviewing chunk {idx+1}/{total_chunks} ({chunk_id}) "
                f"with {len(chunk_blocks)} blocks, {len(chunk_candidates)} candidates...\n"
            )
            sys.stderr.flush()

            verdicts, discoveries, failures, chunk_retries, chunk_successes = (
                self._review_chunk_with_retries(
                    chunk_id,
                    chunk_blocks,
                    chunk_candidates,
                    system_prompt,
                    cancellation,
                )
            )

            all_verdicts.extend(verdicts)
            all_discoveries.extend(discoveries)
            retried_chunks += chunk_retries
            successful_attempts += chunk_successes

            if not failures:
                reviewed_chunks += 1
                if chunk_retries:
                    recovered_chunks += 1
            else:
                failed_chunk_ids.append(chunk_id)
                for failure_type, block_ids in failures:
                    failed_block_ids.update(block_ids)
                    failure_counts[failure_type] += 1

            if progress is not None:
                progress(idx + 1, total_chunks)

        if chunks and successful_attempts == 0:
            raise ValueError("MODEL_FULL_REVIEW_FAILED")

        return FullReviewResult(
            verdicts=tuple(all_verdicts),
            discoveries=tuple(all_discoveries),
            total_chunks=total_chunks,
            reviewed_chunks=reviewed_chunks,
            failed_chunk_ids=tuple(failed_chunk_ids),
            failed_block_ids=tuple(sorted(failed_block_ids)),
            timeout_chunks=failure_counts["timeout"],
            invalid_output_chunks=failure_counts["invalid_output"],
            inference_error_chunks=failure_counts["inference_error"],
            retried_chunks=retried_chunks,
            recovered_chunks=recovered_chunks,
        )

    # ------------------------------------------------------------------
    # Retry logic
    # ------------------------------------------------------------------

    def _review_chunk_with_retries(
        self,
        chunk_id: str,
        chunk_blocks: list[Block],
        chunk_candidates: list[ReviewCandidate],
        system_prompt: str,
        cancellation: CancellationToken,
        depth: int = 0,
    ) -> tuple[
        list[ClassifierVerdict],
        list[DiscoveryProposal],
        list[tuple[str, frozenset[str]]],
        int,  # retry count
        int,  # successful attempt count
    ]:
        """Try to review a chunk; on retryable failure, split in half and
        recurse up to ``_MAX_SPLIT_DEPTH`` levels."""
        # --- Attempt the chunk ---
        verdicts, discoveries, failure, exc = self._try_single_chunk(
            chunk_id, chunk_blocks, chunk_candidates, system_prompt, cancellation
        )

        if failure is None:
            return verdicts, discoveries, [], 0, 1

        block_ids = frozenset(b.id for b in chunk_blocks)

        # Not retryable or max depth reached → give up
        if exc is None or not _is_retryable(failure, exc) or depth >= _MAX_SPLIT_DEPTH:
            return [], [], [(failure, block_ids)], 0, 0

        # Cannot split a single-block chunk further
        if len(chunk_blocks) <= 1:
            return [], [], [(failure, block_ids)], 0, 0

        # --- Split chunk in half and retry each sub-chunk ---
        mid = len(chunk_blocks) // 2
        left_blocks = chunk_blocks[:mid]
        right_blocks = chunk_blocks[mid:]

        candidates_by_block: dict[str, list[ReviewCandidate]] = {}
        for c in chunk_candidates:
            candidates_by_block.setdefault(c.block_id, []).append(c)
        left_candidates = [
            c for b in left_blocks for c in candidates_by_block.get(b.id, [])
        ]
        right_candidates = [
            c for b in right_blocks for c in candidates_by_block.get(b.id, [])
        ]

        sys.stderr.write(
            f"[SoatVan-CloudAI] Retrying {chunk_id}: splitting into "
            f"{len(left_blocks)}+{len(right_blocks)} blocks (depth={depth+1})\n"
        )
        sys.stderr.flush()

        all_verdicts: list[ClassifierVerdict] = []
        all_discoveries: list[DiscoveryProposal] = []
        all_failures: list[tuple[str, frozenset[str]]] = []
        retry_count = 1
        successful_attempts = 0

        for sub_id, sub_blocks, sub_candidates in (
            (f"{chunk_id}_L", left_blocks, left_candidates),
            (f"{chunk_id}_R", right_blocks, right_candidates),
        ):
            cancellation.raise_if_cancelled()
            v, d, f_list, nested_retries, sub_successes = (
                self._review_chunk_with_retries(
                    sub_id,
                    sub_blocks,
                    sub_candidates,
                    system_prompt,
                    cancellation,
                    depth + 1,
                )
            )
            all_verdicts.extend(v)
            all_discoveries.extend(d)
            all_failures.extend(f_list)
            retry_count += nested_retries
            successful_attempts += sub_successes

        return all_verdicts, all_discoveries, all_failures, retry_count, successful_attempts

    def _try_single_chunk(
        self,
        chunk_id: str,
        chunk_blocks: list[Block],
        chunk_candidates: list[ReviewCandidate],
        system_prompt: str,
        cancellation: CancellationToken,
    ) -> tuple[
        list[ClassifierVerdict],
        list[DiscoveryProposal],
        str | None,       # failure type or None on success
        Exception | None,  # original exception or None
    ]:
        """Attempt a single LLM call for one chunk. Returns results + error info."""
        try:
            cancellation.raise_if_cancelled()
            user_content = self._format_user_content(
                chunk_blocks, chunk_candidates
            )
            raw_json = call_llm(self._config, system_prompt, user_content)

            sys.stderr.write(
                f"[SoatVan-CloudAI] Chunk {chunk_id} raw response: {raw_json}\n"
            )
            sys.stderr.flush()

            verdicts, discoveries = parse_llm_response(
                raw_json, chunk_blocks, chunk_candidates
            )

            sys.stderr.write(
                f"[SoatVan-CloudAI] Chunk {chunk_id} parsed: "
                f"{len(discoveries)} discoveries, {len(verdicts)} verdicts\n"
            )
            sys.stderr.flush()

            return verdicts, discoveries, None, None

        except Exception as exc:
            # Re-raise cancellations — they must propagate immediately
            try:
                cancellation.raise_if_cancelled()
            except Exception as cancel_exc:
                raise cancel_exc from exc

            failure = _classify_error(exc)
            sys.stderr.write(
                f"[SoatVan-CloudAI] ERROR in chunk {chunk_id} "
                f"[{failure}]: {exc}\n"
            )
            sys.stderr.flush()

            # Brief backoff before potential retry
            time.sleep(_RETRY_BACKOFF_SECONDS)

            return [], [], failure, exc

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_user_content(
        blocks: list[Block],
        candidates: list[ReviewCandidate],
    ) -> str:
        """Build the ``user`` message content from blocks and candidates."""
        content_lines = [
            f'<segment id="{b.id}" role="target">{b.text}</segment>'
            for b in blocks
        ]
        user_content = "\n".join(content_lines)
        if candidates:
            cand_lines = [
                f'<candidate id="{c.candidate_id}" segment_id="{c.block_id}" '
                f'source_text="{c.source_text}" suggestion="{c.suggestion}" '
                f'reason_code="{c.reason_code}" />'
                for c in candidates
            ]
            user_content += "\n\nDanh sách candidate:\n" + "\n".join(cand_lines)
        return user_content

    @staticmethod
    def _build_chunks(
        blocks: tuple[Block, ...],
        candidates: tuple[ReviewCandidate, ...],
    ) -> list[tuple[str, list[Block], list[ReviewCandidate]]]:
        """Split blocks into ≤4000-char chunks for the LLM context window."""
        candidates_by_block: dict[str, list[ReviewCandidate]] = {}
        for candidate in candidates:
            candidates_by_block.setdefault(candidate.block_id, []).append(
                candidate
            )

        chunks: list[tuple[str, list[Block], list[ReviewCandidate]]] = []
        current_blocks: list[Block] = []
        current_candidates: list[ReviewCandidate] = []
        current_len = 0
        chunk_idx = 0

        for block in blocks:
            text_len = len(block.text)
            if current_blocks and (current_len + text_len > 4000):
                chunks.append(
                    (f"chunk_{chunk_idx}", current_blocks, current_candidates)
                )
                chunk_idx += 1
                current_blocks = []
                current_candidates = []
                current_len = 0
            current_blocks.append(block)
            current_candidates.extend(
                candidates_by_block.get(block.id, [])
            )
            current_len += text_len

        if current_blocks:
            chunks.append(
                (f"chunk_{chunk_idx}", current_blocks, current_candidates)
            )
        return chunks


def supports_full_review() -> bool:
    return True
