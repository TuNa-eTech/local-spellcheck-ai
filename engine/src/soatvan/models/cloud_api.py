"""Cloud AI reviewer — thin orchestrator.

Builds review chunks, delegates HTTP calls to ``llm_transport``, delegates
response parsing to ``response_parser``, and aggregates the results.
"""

from __future__ import annotations

import sys
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
        all_verdicts: list[ClassifierVerdict] = []
        all_discoveries: list[DiscoveryProposal] = []
        failed_chunk_ids: list[str] = []

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
            try:
                # 1. Call LLM (transport layer)
                user_content = self._format_user_content(
                    chunk_blocks, chunk_candidates
                )
                raw_json = call_llm(self._config, system_prompt, user_content)

                sys.stderr.write(
                    f"[SoatVan-CloudAI] Chunk {chunk_id} raw response: {raw_json}\n"
                )
                sys.stderr.flush()

                # 2. Parse response (parser layer)
                verdicts, discoveries = parse_llm_response(
                    raw_json, chunk_blocks, chunk_candidates
                )

                sys.stderr.write(
                    f"[SoatVan-CloudAI] Chunk {chunk_id} parsed: "
                    f"{len(discoveries)} discoveries, {len(verdicts)} verdicts\n"
                )
                sys.stderr.flush()

                all_verdicts.extend(verdicts)
                all_discoveries.extend(discoveries)
                reviewed_chunks += 1
            except Exception as exc:
                sys.stderr.write(
                    f"[SoatVan-CloudAI] ERROR in chunk {chunk_id}: {exc}\n"
                )
                sys.stderr.flush()
                failed_chunk_ids.append(chunk_id)

            if progress is not None:
                progress(idx + 1, total_chunks)

        return FullReviewResult(
            verdicts=tuple(all_verdicts),
            discoveries=tuple(all_discoveries),
            total_chunks=total_chunks,
            reviewed_chunks=reviewed_chunks,
            failed_chunk_ids=tuple(failed_chunk_ids),
        )

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
