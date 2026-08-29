from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from soatvan.checking.domain import Block
from soatvan.checking.localization import canonicalize_llm_edit, localize_llm_edits
from soatvan.custom_rules.ai_config_repository import AiConfigEntry
from soatvan.models.review import (
    DISCOVERY_CATEGORIES,
    DISCOVERY_REASON_CODES,
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


class CloudAiError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def test_ai_connection(config: AiConfigEntry) -> dict[str, Any]:
    if not config.api_key:
        return {
            "ok": False,
            "error": "API_KEY_REQUIRED",
            "message": "API key không được để trống",
        }

    try:
        if config.provider == "gemini":
            url = (
                f"{config.base_url.rstrip('/')}/models/{config.model_name}:generateContent"
                f"?key={config.api_key}"
            )
            payload: dict[str, Any] = {
                "contents": [{"role": "user", "parts": [{"text": "Ping"}]}],
                "generationConfig": {"temperature": 0.0, "maxOutputTokens": 10},
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        else:
            url = f"{config.base_url.rstrip('/')}/chat/completions"
            payload = {
                "model": config.model_name,
                "messages": [{"role": "user", "content": "Ping"}],
                "temperature": 0.0,
                "max_tokens": 10,
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {config.api_key}",
                },
                method="POST",
            )

        with urllib.request.urlopen(req, timeout=15) as response:
            _ = json.loads(response.read().decode("utf-8"))
            return {
                "ok": True,
                "provider": config.provider,
                "model": config.model_name,
            }
    except urllib.error.HTTPError as err:
        err_msg = err.read().decode("utf-8", errors="ignore")
        if err.code in (401, 403):
            return {
                "ok": False,
                "error": "API_KEY_INVALID",
                "message": "API key không hợp lệ hoặc không có quyền truy cập",
            }
        if err.code == 404:
            return {
                "ok": False,
                "error": "MODEL_NOT_FOUND",
                "message": f"Không tìm thấy model '{config.model_name}' hoặc sai URL",
            }
        return {
            "ok": False,
            "error": f"HTTP_{err.code}",
            "message": f"Lỗi HTTP {err.code}: {err_msg[:200]}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": "CONNECTION_FAILED",
            "message": f"Không thể kết nối đến máy chủ AI: {exc}",
        }


test_ai_connection.__test__ = False  # type: ignore[attr-defined]



def _extract_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _nth_occurrence(text: str, needle: str, occurrence: int) -> int | None:
    start = 0
    for _ in range(occurrence + 1):
        found = text.find(needle, start)
        if found < 0:
            return None
        start = found + len(needle)
    return found


class CloudAiReviewer:
    def __init__(self, config: AiConfigEntry) -> None:
        self._config = config

    @property
    def version(self) -> str:
        return f"{self._config.provider}:{self._config.model_name}"

    @property
    def minimum_confidence(self) -> float:
        return 0.55

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
            try:
                raw_json = self._call_ai(
                    system_prompt, chunk_blocks, chunk_candidates, cancellation
                )
                verdicts, discoveries = self._parse_response(
                    raw_json, chunk_blocks, chunk_candidates
                )
                all_verdicts.extend(verdicts)
                all_discoveries.extend(discoveries)
                reviewed_chunks += 1
            except Exception:
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

    def _build_chunks(
        self,
        blocks: tuple[Block, ...],
        candidates: tuple[ReviewCandidate, ...],
    ) -> list[tuple[str, list[Block], list[ReviewCandidate]]]:
        candidates_by_block: dict[str, list[ReviewCandidate]] = {}
        for candidate in candidates:
            candidates_by_block.setdefault(candidate.block_id, []).append(candidate)

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
            current_candidates.extend(candidates_by_block.get(block.id, []))
            current_len += text_len

        if current_blocks:
            chunks.append(
                (f"chunk_{chunk_idx}", current_blocks, current_candidates)
            )
        return chunks

    def _call_ai(
        self,
        system_prompt: str,
        blocks: list[Block],
        candidates: list[ReviewCandidate],
        cancellation: CancellationToken,
    ) -> dict[str, Any]:
        cancellation.raise_if_cancelled()
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

        if self._config.provider == "gemini":
            url = (
                f"{self._config.base_url.rstrip('/')}/models/{self._config.model_name}:generateContent"
                f"?key={self._config.api_key}"
            )
            payload: dict[str, Any] = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": f"{system_prompt}\n\n{user_content}"}
                        ],
                    }
                ],
                "generationConfig": {
                    "temperature": self._config.temperature,
                    "responseMimeType": "application/json",
                },
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        else:
            url = f"{self._config.base_url.rstrip('/')}/chat/completions"
            payload = {
                "model": self._config.model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "temperature": self._config.temperature,
                "response_format": {"type": "json_object"},
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._config.api_key}",
                },
                method="POST",
            )

        with urllib.request.urlopen(
            req, timeout=self._config.timeout_seconds
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if self._config.provider == "gemini":
                text = data["candidates"][0]["content"]["parts"][0]["text"]
            else:
                text = data["choices"][0]["message"]["content"]
            parsed = json.loads(_extract_json_text(text))
            if isinstance(parsed, dict):
                return parsed
            return {}

    def _parse_response(
        self,
        data: dict[str, Any],
        blocks: list[Block],
        candidates: list[ReviewCandidate],
    ) -> tuple[list[ClassifierVerdict], list[DiscoveryProposal]]:
        block_map = {b.id: b for b in blocks}
        discoveries: list[DiscoveryProposal] = []
        verdicts: list[ClassifierVerdict] = []
        seen_discoveries: set[tuple[str, int, int, str]] = set()

        for item in data.get("discoveries", []):
            if not isinstance(item, dict):
                continue
            seg_id = str(item.get("segment_id", ""))
            if seg_id not in block_map:
                continue
            block = block_map[seg_id]
            source_text = str(item.get("source_text", ""))
            suggestion = str(item.get("suggestion", ""))
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
                continue

            occ_idx = int(item.get("occurrence_index", 0))
            anchor_start = _nth_occurrence(block.text, source_text, occ_idx)
            if anchor_start is None:
                anchor_start = block.text.find(source_text)
            if anchor_start < 0:
                # If original broad source_text not found, try searching by first localized source
                first_local_src = localized_edits[0][1]
                anchor_start = block.text.find(first_local_src)
                if anchor_start < 0:
                    continue
                # Offset relative to first local src
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
                    # Fallback locate
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
