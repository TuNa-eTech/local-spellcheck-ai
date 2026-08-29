from __future__ import annotations

import json
import sys
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


def _normalize_base_url(provider: str, base_url: str) -> str:
    url = base_url.strip().rstrip("/")
    if provider == "gemini":
        if url == "https://generativelanguage.googleapis.com":
            url = "https://generativelanguage.googleapis.com/v1beta"
    elif provider == "openai":
        if url.endswith("/chat/completions"):
            url = url[:-len("/chat/completions")].rstrip("/")
        elif url in {"https://api.openai.com", "https://api.deepseek.com"}:
            url = f"{url}/v1"
    return url


def _parse_openai_text_response(raw_body: str) -> str:
    raw_body = raw_body.strip()
    if not raw_body:
        return ""
    if raw_body.startswith("data:"):
        chunks: list[str] = []
        for line in raw_body.splitlines():
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            data_part = line[len("data:") :].strip()
            if data_part == "[DONE]":
                continue
            try:
                chunk_obj = json.loads(data_part)
                choices = chunk_obj.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content") or choices[0].get("message", {}).get(
                        "content", ""
                    )
                    if content:
                        chunks.append(str(content))
            except Exception:
                continue
        return "".join(chunks)

    obj = json.loads(raw_body)
    if isinstance(obj, dict):
        choices = obj.get("choices", [])
        if choices and isinstance(choices, list) and len(choices) > 0:
            msg = choices[0].get("message", {})
            return str(msg.get("content", ""))
    return ""


def test_ai_connection(config: AiConfigEntry) -> dict[str, Any]:
    if not config.api_key:
        return {
            "ok": False,
            "error": "API_KEY_REQUIRED",
            "message": "API key không được để trống",
        }

    base_url = _normalize_base_url(config.provider, config.base_url)
    default_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "SoatVan/0.1.5 (Desktop; vi-VN)",
    }

    try:
        if config.provider == "gemini":
            url = (
                f"{base_url}/models/{config.model_name}:generateContent"
                f"?key={config.api_key}"
            )
            payload: dict[str, Any] = {
                "contents": [{"role": "user", "parts": [{"text": "Ping"}]}],
                "generationConfig": {"temperature": 0.0, "maxOutputTokens": 10},
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=default_headers,
                method="POST",
            )
        else:
            url = f"{base_url}/chat/completions"
            payload = {
                "model": config.model_name,
                "messages": [{"role": "user", "content": "Ping"}],
                "temperature": 0.0,
                "max_tokens": 10,
                "stream": False,
            }
            headers = dict(default_headers)
            headers["Authorization"] = f"Bearer {config.api_key}"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )

        with urllib.request.urlopen(req, timeout=15) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            if not raw_body.strip():
                return {
                    "ok": False,
                    "error": "EMPTY_RESPONSE",
                    "message": "Máy chủ phản hồi rỗng (vui lòng kiểm tra lại Base URL).",
                }
            if config.provider == "gemini":
                try:
                    _ = json.loads(raw_body)
                except json.JSONDecodeError:
                    snippet = raw_body[:120].replace("\n", " ")
                    return {
                        "ok": False,
                        "error": "INVALID_JSON",
                        "message": f"Máy chủ không trả về JSON hợp lệ: {snippet}",
                    }
            else:
                text_content = _parse_openai_text_response(raw_body)
                if not text_content and not raw_body.strip().startswith("{"):
                    snippet = raw_body[:120].replace("\n", " ")
                    return {
                        "ok": False,
                        "error": "INVALID_JSON",
                        "message": f"Máy chủ không trả về JSON hợp lệ: {snippet}",
                    }
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
                "message": f"Không tìm thấy model '{config.model_name}' hoặc sai URL ({err.url})",
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
            sys.stderr.write(
                f"[SoatVan-CloudAI] Reviewing chunk {idx+1}/{total_chunks} ({chunk_id}) "
                f"with {len(chunk_blocks)} blocks, {len(chunk_candidates)} candidates...\n"
            )
            sys.stderr.flush()
            try:
                raw_json = self._call_ai(
                    system_prompt, chunk_blocks, chunk_candidates, cancellation
                )
                verdicts, discoveries = self._parse_response(
                    raw_json, chunk_blocks, chunk_candidates
                )
                sys.stderr.write(
                    f"[SoatVan-CloudAI] Chunk {chunk_id} parsed: {len(discoveries)} discoveries, "
                    f"{len(verdicts)} verdicts\n"
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

        base_url = _normalize_base_url(self._config.provider, self._config.base_url)
        default_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "SoatVan/0.1.5 (Desktop; vi-VN)",
        }

        if self._config.provider == "gemini":
            url = (
                f"{base_url}/models/{self._config.model_name}:generateContent"
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
                headers=default_headers,
                method="POST",
            )
        else:
            url = f"{base_url}/chat/completions"
            payload = {
                "model": self._config.model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "temperature": self._config.temperature,
                "response_format": {"type": "json_object"},
                "stream": False,
            }
            headers = dict(default_headers)
            headers["Authorization"] = f"Bearer {self._config.api_key}"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )

        try:
            with urllib.request.urlopen(
                req, timeout=self._config.timeout_seconds
            ) as resp:
                raw_body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as err:
            if (
                err.code == 400
                and self._config.provider != "gemini"
                and "response_format" in payload
            ):
                payload_no_rf = dict(payload)
                del payload_no_rf["response_format"]
                req_retry = urllib.request.Request(
                    url,
                    data=json.dumps(payload_no_rf).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(
                    req_retry, timeout=self._config.timeout_seconds
                ) as resp:
                    raw_body = resp.read().decode("utf-8", errors="replace")
            else:
                raise

        if self._config.provider == "gemini":
            data = json.loads(raw_body)
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        else:
            text = _parse_openai_text_response(raw_body)
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
