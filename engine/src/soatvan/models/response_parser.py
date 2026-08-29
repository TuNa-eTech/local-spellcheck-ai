"""Pure-function response parser for LLM review output.

Takes the raw JSON dict returned by the LLM transport layer and the chunk's
blocks/candidates, then produces typed ``DiscoveryProposal`` and
``ClassifierVerdict`` objects.  All anchor-resolution, segment-id fallback,
and localization logic lives here — completely independent of HTTP/networking.
"""

from __future__ import annotations

import sys
from typing import Any

from soatvan.checking.domain import Block
from soatvan.checking.localization import canonicalize_llm_edit, localize_llm_edits
from soatvan.models.review import DISCOVERY_CATEGORIES, DISCOVERY_REASON_CODES
from soatvan.workflow.ports import (
    ClassifierVerdict,
    DiscoveryProposal,
    ReviewCandidate,
)


def _nth_occurrence(text: str, needle: str, occurrence: int) -> int | None:
    start = 0
    for _ in range(occurrence + 1):
        found = text.find(needle, start)
        if found < 0:
            return None
        start = found + len(needle)
    return found


def parse_llm_response(
    data: dict[str, Any],
    blocks: list[Block],
    candidates: list[ReviewCandidate],
) -> tuple[list[ClassifierVerdict], list[DiscoveryProposal]]:
    """Parse a raw LLM JSON response into typed verdicts and discoveries.

    Handles the common case where the LLM omits ``segment_id`` by scanning the
    chunk's blocks for a text match on ``source_text``.
    """
    block_map = {b.id: b for b in blocks}
    discoveries: list[DiscoveryProposal] = []
    verdicts: list[ClassifierVerdict] = []
    seen_discoveries: set[tuple[str, int, int, str]] = set()

    for item in data.get("discoveries", []):
        if not isinstance(item, dict):
            continue
        source_text = str(item.get("source_text", ""))
        suggestion = str(item.get("suggestion", ""))
        if not source_text or not suggestion:
            continue

        category = str(item.get("category", "spelling"))
        reason_code = str(item.get("reason_code", category))
        if category not in DISCOVERY_CATEGORIES:
            category = "spelling"
        if reason_code not in DISCOVERY_REASON_CODES:
            reason_code = category

        category, reason_code = canonicalize_llm_edit(
            source_text, suggestion, category, reason_code
        )
        localized_edits = localize_llm_edits(
            source_text, suggestion, reason_code
        )
        if not localized_edits:
            sys.stderr.write(
                f"[SoatVan-CloudAI] Ignored item: localization rejected edit "
                f"{source_text!r} -> {suggestion!r} ({reason_code})\n"
            )
            continue

        # --- Resolve segment_id (auto-detect when LLM omits it) ---
        seg_id = str(item.get("segment_id", ""))
        block = block_map.get(seg_id)
        if block is None:
            for candidate_block in blocks:
                if source_text in candidate_block.text or (
                    localized_edits
                    and localized_edits[0][1] in candidate_block.text
                ):
                    block = candidate_block
                    seg_id = candidate_block.id
                    break

        if block is None:
            sys.stderr.write(
                f"[SoatVan-CloudAI] Ignored item: source_text "
                f"{source_text!r} not found in any chunk block\n"
            )
            continue

        # --- Anchor the edit position in the block text ---
        occ_idx = int(item.get("occurrence_index", 0))
        anchor_start = _nth_occurrence(block.text, source_text, occ_idx)
        if anchor_start is None:
            anchor_start = block.text.find(source_text)
        if anchor_start < 0:
            first_local_src = localized_edits[0][1]
            anchor_start = block.text.find(first_local_src)
            if anchor_start < 0:
                continue
            rel_offset = localized_edits[0][0]
            base_start = anchor_start - rel_offset
        else:
            base_start = anchor_start

        for rel_start, local_src, local_sug in localized_edits:
            start = base_start + rel_start
            end = start + len(local_src)
            if start < 0 or end > len(block.text):
                continue
            # Sanity check text at position
            if block.text[start:end] != local_src:
                loc_find = block.text.find(local_src, max(0, start - 10))
                if loc_find < 0:
                    loc_find = block.text.find(local_src)
                if loc_find < 0:
                    continue
                start = loc_find
                end = start + len(local_src)

            key = (seg_id, start, end, local_sug)
            if key in seen_discoveries:
                continue
            seen_discoveries.add(key)

            discoveries.append(
                DiscoveryProposal(
                    block_id=seg_id,
                    start=start,
                    end=end,
                    source_text=local_src,
                    suggestion=local_sug,
                    category=category,
                    reason_code=reason_code,
                    confidence=float(item.get("confidence", 0.9)),
                )
            )

    # --- Verdicts for existing candidates ---
    candidate_ids = {c.candidate_id for c in candidates}
    for v in data.get("verdicts", []):
        if isinstance(v, dict) and "candidate_id" in v and "verdict" in v:
            cid = str(v["candidate_id"])
            verdict_str = str(v["verdict"])
            if verdict_str in ("keep", "drop") and (
                not candidate_ids or cid in candidate_ids
            ):
                verdicts.append(
                    ClassifierVerdict(
                        candidate_id=cid,
                        verdict=verdict_str,
                        confidence=float(v.get("confidence", 0.9)),
                    )
                )

    return verdicts, discoveries
