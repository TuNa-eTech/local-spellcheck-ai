from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from soatvan.checking import Block, Finding, Preset, RuleEngine
from soatvan.checking.localization import (
    canonicalize_llm_edit,
    localize_llm_edit,
    localize_llm_edits,
)
from soatvan.models import LlamaCppClassifier
from soatvan.models.classifier import REVIEW_REPEAT_PENALTY
from soatvan.models.review import (
    MAX_EDIT_LENGTH,
    MAX_REVIEW_ITEMS,
    REVIEW_SCHEMA,
    REVIEW_SYSTEM_PROMPT,
    ReviewChunk,
    ReviewSegment,
    _preferred_boundary,
    parse_review_content,
    plan_review_chunks,
    review_messages,
    split_review_chunk,
)
from soatvan.models.review_budget import (
    DEFAULT_REVIEW_CHUNK_TOKENS,
    MAX_REVIEW_OUTPUT_TOKENS,
    MIN_REVIEW_OUTPUT_TOKENS,
    ReviewBudget,
    review_budget_from_manifest,
)
from soatvan.workflow import ProcessDocument, ProcessRequest
from soatvan.workflow.ports import (
    AnnotationResult,
    ClassifierVerdict,
    DiscoveryProposal,
    FullReviewResult,
)
from soatvan.workflow.process import (
    _apply_full_review,
    _limit_review_findings,
    _merge_review_findings,
)


class Token:
    def raise_if_cancelled(self) -> None:
        return None


class Runtime:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []
        self.counted_messages: list[list[dict[str, str]]] = []

    def count_tokens(self, value: str) -> int:
        return max(1, len(value) // 4)

    def count_chat_tokens(self, messages: list[dict[str, str]]) -> int:
        self.counted_messages.append(messages)
        return sum(self.count_tokens(message["content"]) for message in messages) + 32

    def create_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"choices": [{"message": {"content": self.content}}]}


def request_counter(count_tokens):
    def count(chunk: ReviewChunk) -> int:
        return count_tokens(chunk.payload()) + count_tokens(chunk.custom_prompt)

    return count


def review_budget(document_tokens: int, input_tokens: int = 3072) -> ReviewBudget:
    return ReviewBudget(
        context_tokens=input_tokens + 1024,
        response_tokens=768,
        safety_tokens=256,
        document_tokens=document_tokens,
    )


class PartiallyFailingRuntime(Runtime):
    def create_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if len(self.calls) == 2:
            raise RuntimeError("transient inference failure")
        return {"choices": [{"message": {"content": self.content}}]}


class RetryStillFailingRuntime(Runtime):
    def create_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if 2 <= len(self.calls) <= 14:
            raise RuntimeError("persistent inference failure")
        return {"choices": [{"message": {"content": self.content}}]}


class NestedRetryRuntime(Runtime):
    def create_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if len(self.calls) in {2, 3}:
            raise RuntimeError("nested transient inference failure")
        return {"choices": [{"message": {"content": self.content}}]}


class PromptOverflowRuntime(Runtime):
    def __init__(self, base_tokens: int, custom_tokens: int) -> None:
        super().__init__('{"discoveries":[]}')
        self.base_tokens = base_tokens
        self.custom_tokens = custom_tokens

    def count_tokens(self, value: str) -> int:
        raise AssertionError(f"document tokenization should not run: {value!r}")

    def count_chat_tokens(self, messages: list[dict[str, str]]) -> int:
        self.counted_messages.append(messages)
        has_custom_rule = "<custom_rules>" in messages[0]["content"]
        return self.custom_tokens if has_custom_rule else self.base_tokens

    def create_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError(f"inference should not run: {kwargs!r}")


class Dictionary:
    def ignored_words(self) -> frozenset[str]:
        return frozenset()


class Documents:
    def __init__(self, blocks: list[Block]) -> None:
        self.blocks = blocks
        self.written = []

    def read_blocks(self, _: Path) -> list[Block]:
        return self.blocks

    def inspect(self, _: Path) -> dict[str, object]:
        return {}

    def write_annotations(
        self, source: Path, target: Path, findings, cancellation=None
    ) -> AnnotationResult:
        del source, target, cancellation
        self.written = list(findings)
        return AnnotationResult(tuple(item.id for item in self.written))


class PartiallyWritableDocuments(Documents):
    def __init__(self, blocks: list[Block], writable_block_ids: set[str]) -> None:
        super().__init__(blocks)
        self.writable_block_ids = writable_block_ids

    def write_annotations(
        self, source: Path, target: Path, findings, cancellation=None
    ) -> AnnotationResult:
        del source, target, cancellation
        self.written = [item for item in findings if item.block_id in self.writable_block_ids]
        return AnnotationResult(tuple(item.id for item in self.written))


def test_review_prompts_keep_custom_rules_inside_the_output_contract() -> None:
    assert "không được đổi định dạng" in REVIEW_SYSTEM_PROMPT
    assert "yêu cầu trả cả câu" in REVIEW_SYSTEM_PROMPT
    assert "Chỉ trả JSON" in REVIEW_SYSTEM_PROMPT


def test_review_messages_injects_custom_prompt_into_system_prompt() -> None:
    segment = ReviewSegment("seg-1", "blk-1", 0, 0, "Nội dung kiểm tra", "p")
    chunk = ReviewChunk("chunk-1", (segment,), "Luật riêng của người dùng")
    messages = review_messages(chunk)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "## QUY TẮC RIÊNG CỦA NGƯỜI DÙNG (BẮT BUỘC TUÂN THỦ):" in messages[0]["content"]
    assert "<custom_rules>\nLuật riêng của người dùng\n</custom_rules>" in messages[0]["content"]
    # The rule text belongs to the system turn; the user turn is document text
    # and nothing else, so a rule can never be read back as document content.
    assert messages[1]["content"] == "1|Nội dung kiểm tra"


def test_review_payload_numbers_segments_in_the_order_the_parser_reads_them() -> None:
    first = ReviewSegment("p1@0:5", "document:p1", 1, 0, "Đoạn hai", "paragraph")
    second = ReviewSegment("p0@0:5", "document:p0", 0, 0, "Đoạn một", "paragraph")
    chunk = ReviewChunk("chunk-1", (first, second), "")

    assert chunk.payload() == "1|Đoạn một\n2|Đoạn hai"
    assert [item.segment_id for item in chunk.ordered_targets()] == ["p0@0:5", "p1@0:5"]


def test_review_budget_keeps_output_reserve_and_clamps_only_document_capacity() -> None:
    budget = ReviewBudget(
        context_tokens=2048,
        response_tokens=768,
        safety_tokens=256,
        document_tokens=1200,
    )

    assert budget.input_tokens == 1024
    assert budget.document_limit(200) == 824
    assert budget.response_tokens == 768


def test_review_budget_requires_a_minimum_input_window() -> None:
    exact = ReviewBudget(1088, 768, 256, 1200)
    assert exact.input_tokens == 64
    with pytest.raises(ValueError, match="MODEL_REVIEW_CONTEXT_TOO_SMALL"):
        ReviewBudget(1087, 768, 256, 1200)


def test_review_reason_is_canonicalized_from_the_actual_edit() -> None:
    assert canonicalize_llm_edit("thọai", "thoại", "compound_word", "compound_word") == (
        "spelling",
        "diacritic",
    )
    assert canonicalize_llm_edit("Nội  dung", "Nội dung", "grammar", "grammar") == (
        "technical",
        "spacing",
    )


def test_review_planner_covers_every_character_once() -> None:
    text = " ".join(f"từ{i}" for i in range(180))
    blocks = (Block("document:p0", text), Block("document:p1", "Đoạn kết."))

    def count_tokens(value: str) -> int:
        return max(1, len(value) // 4)

    chunks = plan_review_chunks(
        blocks,
        "",
        review_budget(90),
        count_tokens,
        request_counter(count_tokens),
    )
    assert len(chunks) > 1
    target_segments = [segment for chunk in chunks for segment in chunk.targets]
    rebuilt = "".join(
        item.text
        for item in sorted(
            (segment for segment in target_segments if segment.block_id == "document:p0"),
            key=lambda segment: segment.source_start,
        )
    )
    assert rebuilt == text
    assert len({segment.segment_id for segment in target_segments}) == len(target_segments)


def test_review_planner_groups_short_paragraphs_adaptively() -> None:
    """Short adjacent paragraphs are paired adaptively up to 2 per chunk.

    Reduces inference requests by ~50% for documents with many short paragraphs,
    while staying within safe prompt limits.
    """
    blocks = tuple(Block(f"document:p{index}", f"Đoạn số {index}.") for index in range(6))

    def count_tokens(value: str) -> int:
        return max(1, len(value) // 4)

    chunks = plan_review_chunks(
        blocks,
        "",
        review_budget(4000, 8000),
        count_tokens,
        request_counter(count_tokens),
    )

    assert len(chunks) == 3
    assert all(len(chunk.targets) == 2 for chunk in chunks)
    assert [segment.block_id for chunk in chunks for segment in chunk.targets] == [
        b.id for b in blocks
    ]


def test_review_planner_keeps_long_paragraphs_as_single_chunks() -> None:
    """Paragraphs whose combined length exceeds the limit stay as individual chunks."""
    long_text = "Nội dung đoạn văn bản hành chính dài. " * 20
    blocks = (
        Block("document:p0", long_text),
        Block("document:p1", long_text),
    )

    def count_tokens(value: str) -> int:
        return max(1, len(value) // 4)

    chunks = plan_review_chunks(
        blocks,
        "",
        review_budget(4000, 8000),
        count_tokens,
        request_counter(count_tokens),
    )

    assert len(chunks) == 2
    assert all(len(chunk.targets) == 1 for chunk in chunks)
    assert [chunk.targets[0].block_id for chunk in chunks] == ["document:p0", "document:p1"]


def test_review_chunk_budget_caps_document_text_without_subtracting_prompt() -> None:
    text = "x" * 150
    count_tokens = len
    budget = ReviewBudget(10_000, 768, 256, 64)
    chunks = plan_review_chunks(
        (Block("document:p0", text),),
        "quy tắc " * 40,
        budget,
        count_tokens,
        request_counter(count_tokens),
    )

    assert "".join(segment.text for chunk in chunks for segment in chunk.targets) == text
    target_sizes = [
        sum(count_tokens(segment.text) for segment in chunk.targets) for chunk in chunks
    ]
    assert target_sizes[0] == 64
    assert all(size <= 64 for size in target_sizes)


def test_long_custom_rule_keeps_document_boundaries_when_context_is_sufficient() -> None:
    block = Block("document:p0", "x" * 150)
    count_tokens = len
    budget = ReviewBudget(10_000, 768, 256, 64)

    without_rule = plan_review_chunks(
        (block,), "", budget, count_tokens, request_counter(count_tokens)
    )
    with_rule = plan_review_chunks(
        (block,), "q" * 1000, budget, count_tokens, request_counter(count_tokens)
    )

    assert [item.segment_id for chunk in without_rule for item in chunk.targets] == [
        item.segment_id for chunk in with_rule for item in chunk.targets
    ]


def test_review_segmenter_prefers_a_sentence_boundary_over_later_spaces() -> None:
    text = "0123456789. sau nữa tiếp tục"
    expected = text.index(". ") + 2
    assert _preferred_boundary(text, 0, 18) == expected


def test_failed_review_chunk_splits_without_losing_source_offsets() -> None:
    target = ReviewSegment(
        "document:p0@10:39",
        "document:p0",
        0,
        10,
        "Câu thứ nhất. Câu thứ hai.",
        "paragraph",
    )
    retries = split_review_chunk(ReviewChunk("chunk-1", (target,), ""))

    assert retries
    left, right = retries
    assert left.targets[0].text + right.targets[0].text == target.text
    assert left.targets[0].source_start == target.source_start
    assert left.targets[0].source_end == right.targets[0].source_start
    assert right.targets[0].source_end == target.source_end


def test_failed_multi_segment_chunk_splits_down_the_middle() -> None:
    targets = tuple(
        ReviewSegment(f"document:p{i}@0:6", f"document:p{i}", i, 0, f"Đoạn {i}", "paragraph")
        for i in range(4)
    )
    retries = split_review_chunk(ReviewChunk("chunk-1", targets, "rule"))

    assert retries
    left, right = retries
    assert [item.segment_id for item in left.targets] == ["document:p0@0:6", "document:p1@0:6"]
    assert [item.segment_id for item in right.targets] == ["document:p2@0:6", "document:p3@0:6"]
    assert left.custom_prompt == right.custom_prompt == "rule"


def test_review_planning_can_be_cancelled_between_blocks() -> None:
    calls = 0

    def cancel() -> None:
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        plan_review_chunks(
            tuple(Block(f"document:p{index}", "Nội dung") for index in range(10)),
            "",
            review_budget(120),
            lambda value: max(1, len(value) // 4),
            request_counter(lambda value: max(1, len(value) // 4)),
            cancellation=cancel,
        )


def test_review_parser_anchors_a_finding_to_the_line_the_model_reported() -> None:
    first = ReviewSegment("p0@0:13", "document:p0", 0, 0, "Tôi dang làm.", "paragraph")
    second = ReviewSegment("p1@0:12", "document:p1", 1, 0, "Đừng sửa tôi", "paragraph")
    chunk = ReviewChunk("chunk-1", (first, second), "")

    discoveries = parse_review_content(
        json.dumps([{"l": 1, "s": "dang", "r": "đang"}], ensure_ascii=False), chunk
    )

    assert discoveries is not None
    assert [(item.block_id, item.start, item.end, item.suggestion) for item in discoveries] == [
        ("document:p0", 4, 8, "đang")
    ]


def test_review_parser_drops_a_quote_that_appears_in_no_segment() -> None:
    target = ReviewSegment("p0@0:13", "document:p0", 0, 0, "Tôi dang làm.", "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), "")

    assert parse_review_content('[{"l":1,"s":"không có","r":"x"}]', chunk) == ()


def test_review_parser_recovers_from_a_miscounted_line_only_when_it_is_unambiguous() -> None:
    first = ReviewSegment("p0@0:13", "document:p0", 0, 0, "Tôi dang làm.", "paragraph")
    second = ReviewSegment("p1@0:16", "document:p1", 1, 0, "Anh ấy dang đi.", "paragraph")
    unique = ReviewChunk("chunk-1", (first,), "")
    ambiguous = ReviewChunk("chunk-2", (first, second), "")

    # Line 9 does not exist. One segment holds the quote, so it still anchors.
    recovered = parse_review_content('[{"l":9,"s":"dang","r":"đang"}]', unique)
    assert recovered is not None
    assert [(item.block_id, item.start) for item in recovered] == [("document:p0", 4)]

    # Two segments hold it, so guessing would scatter highlights. Drop instead.
    assert parse_review_content('[{"l":9,"s":"dang","r":"đang"}]', ambiguous) == ()


def test_review_parser_walks_repeats_onto_successive_occurrences() -> None:
    text = "Ban hành ban hành ban hành."
    target = ReviewSegment(f"p0@0:{len(text)}", "document:p0", 0, 0, text, "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), "")

    discoveries = parse_review_content(
        json.dumps([{"l": 1, "s": "ban hành", "r": "ban-hành"}] * 2, ensure_ascii=False),
        chunk,
    )

    assert discoveries is not None
    assert [(item.start, item.end) for item in discoveries] == [(9, 17), (18, 26)]
    assert all(text[item.start : item.end] == item.source_text for item in discoveries)


def test_review_parser_deduplicates_an_identical_anchor() -> None:
    target = ReviewSegment("p0@0:12", "document:p0", 0, 0, "Nội dung sai", "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), "")

    discoveries = parse_review_content(
        '[{"l":1,"s":"sai","r":"đúng"},{"l":1,"s":"sai","r":"đúng"}]', chunk
    )

    assert discoveries is not None
    assert len(discoveries) == 1


def test_review_parser_derives_category_from_the_shape_of_the_edit() -> None:
    text = "Số điện thọai và  khoảng trắng"
    target = ReviewSegment(f"p0@0:{len(text)}", "document:p0", 0, 0, text, "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), "")

    discoveries = parse_review_content(
        json.dumps(
            [
                {"l": 1, "s": "thọai", "r": "thoại"},
                {"l": 1, "s": "và  khoảng", "r": "và khoảng"},
            ],
            ensure_ascii=False,
        ),
        chunk,
    )

    assert discoveries is not None
    assert [(item.category, item.reason_code) for item in discoveries] == [
        ("spelling", "diacritic"),
        ("technical", "spacing"),
    ]


def test_review_parser_drops_one_bad_item_without_failing_the_whole_chunk() -> None:
    target = ReviewSegment("p0@0:12", "document:p0", 0, 0, "Nội dung sai", "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), "")

    discoveries = parse_review_content(
        json.dumps(
            [
                {"l": "một", "s": "sai", "r": "đúng"},
                {"l": 1, "s": "sai", "r": "sai"},
                {"l": 1, "s": "x" * (MAX_EDIT_LENGTH + 1), "r": "y"},
                {"l": 1, "s": "sai", "r": "đúng"},
            ],
            ensure_ascii=False,
        ),
        chunk,
    )

    # A malformed item costs itself, never the chunk: re-running the inference
    # is the expensive half of this pipeline.
    assert discoveries is not None
    assert [(item.source_text, item.suggestion) for item in discoveries] == [("sai", "đúng")]


def test_review_parser_returns_none_only_when_the_response_is_unusable() -> None:
    target = ReviewSegment("p0@0:12", "document:p0", 0, 0, "Nội dung sai", "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), "")

    assert parse_review_content("not json", chunk) is None
    assert parse_review_content('{"unexpected": 1}', chunk) is None
    assert parse_review_content("[]", chunk) == ()
    # Truncated output is what a runaway generation leaves behind.
    assert parse_review_content('[{"l":1,"s":"sai","r":"đ', chunk) is None


def test_review_parser_repairs_a_raw_newline_inside_a_json_string() -> None:
    """The exact shape that cost chunk-38 a retry, reproduced from the log.

    gemma-4-e2b emitted a real U+000A where it meant a space, inside the quoted
    source text. The response was complete — it ended in ``"}]`` at 120 of its
    512 allowed tokens — but ``json.loads`` rejects an unescaped control
    character, so the chunk was re-inferred as two halves for nothing.
    """
    text = "Dịch vụ ăn uống, cà phê, bán lẻ kết hợp dịch vụ tiện ích."
    target = ReviewSegment(f"p0@0:{len(text)}", "document:p0", 0, 0, text, "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), "")
    raw = '[{"l":1,"s":"ăn uống,\ncà phê","r":"ăn uống, cà-phê"}]'

    with pytest.raises(json.JSONDecodeError, match="Invalid control character"):
        json.loads(raw)

    discoveries = parse_review_content(raw, chunk)

    assert discoveries is not None
    assert [(item.source_text, item.suggestion) for item in discoveries] == [
        ("ăn uống, cà phê", "ăn uống, cà-phê")
    ]
    assert text[discoveries[0].start : discoveries[0].end] == "ăn uống, cà phê"


def test_review_parser_leaves_a_well_formed_response_untouched() -> None:
    text = "Tôi dang làm."
    target = ReviewSegment(f"p0@0:{len(text)}", "document:p0", 0, 0, text, "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), "")

    # Newlines between tokens are legal JSON whitespace and must not be treated
    # as damage, and an escaped \n inside a string must survive as itself.
    assert parse_review_content('[\n {"l": 1,\n  "s": "dang",\n  "r": "đang"}\n]', chunk) is not None
    escaped = parse_review_content('[{"l":1,"s":"dang","r":"đ\\nang"}]', chunk)
    assert escaped is not None
    assert [item.suggestion for item in escaped] == ["đ\nang"]


def test_review_parser_repairs_a_control_character_in_a_truncated_response() -> None:
    text = "Tôi dang làm. Đơn vị đã bổ xung hồ sơ."
    target = ReviewSegment(f"p0@0:{len(text)}", "document:p0", 0, 0, text, "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), "")
    # Both faults at once: an illegal newline in the finished item, and the cap
    # cutting the next one in half.
    raw = '[{"l":1,"s":"Tôi\ndang","r":"Tôi đang"},{"l":1,"s":"bổ xung","r":"bổ s'

    discoveries = parse_review_content(raw, chunk)

    assert discoveries is not None
    assert [(item.source_text, item.suggestion) for item in discoveries] == [
        ("Tôi dang", "Tôi đang")
    ]


def test_review_parser_keeps_the_finished_items_of_a_truncated_response() -> None:
    text = "Tôi dang làm. Đơn vị đã bổ xung hồ sơ."
    target = ReviewSegment(f"p0@0:{len(text)}", "document:p0", 0, 0, text, "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), "")
    # What the token cap leaves behind: one finished item, one cut mid-string.
    truncated = '[{"l":1,"s":"dang","r":"đang"},{"l":1,"s":"bổ xung","r":"bổ s'

    discoveries = parse_review_content(truncated, chunk)

    assert discoveries is not None
    assert [(item.source_text, item.suggestion) for item in discoveries] == [("dang", "đang")]


def test_review_parser_salvage_still_refuses_a_response_with_no_finished_item() -> None:
    target = ReviewSegment("p0@0:12", "document:p0", 0, 0, "Nội dung sai", "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), "")

    # Nothing completed, so there is nothing to keep and the chunk must retry.
    assert parse_review_content('[{"l":1,"s":"sa', chunk) is None
    assert parse_review_content('{"e":[{"l":1,"s":"sa', chunk) is None


def test_review_parser_accepts_the_whole_sentence_quotes_the_model_actually_emits() -> None:
    # gemma-4-e2b quotes the sentence it found the error in whatever the prompt
    # asks for, and llama.cpp never turns the schema's maxLength into a grammar
    # rule. Rejecting those quotes silently dropped every finding it reported.
    text = "Kính gửi uỷ ban nhân dân thành phố hà nội."
    target = ReviewSegment(f"p0@0:{len(text)}", "document:p0", 0, 0, text, "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), "")

    discoveries = parse_review_content(
        json.dumps(
            [{"l": 1, "s": text, "r": "Kính gửi Ủy ban nhân dân thành phố Hà Nội."}],
            ensure_ascii=False,
        ),
        chunk,
    )

    assert discoveries is not None
    assert len(discoveries) == 1
    # The parser anchors the quote; _apply_full_review narrows it to the edits.
    assert text[discoveries[0].start : discoveries[0].end] == text


def test_review_parser_rejects_a_response_beyond_the_item_ceiling() -> None:
    target = ReviewSegment("p0@0:12", "document:p0", 0, 0, "Nội dung sai", "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), "")
    flood = [{"l": 1, "s": "sai", "r": "đúng"}] * (MAX_REVIEW_ITEMS + 1)

    assert parse_review_content(json.dumps(flood), chunk) is None


def test_review_parser_accepts_a_wrapped_array_from_a_small_model() -> None:
    target = ReviewSegment("p0@0:12", "document:p0", 0, 0, "Nội dung sai", "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), "")

    discoveries = parse_review_content('{"e":[{"l":1,"s":"sai","r":"đúng"}]}', chunk)

    assert discoveries is not None
    assert [item.suggestion for item in discoveries] == ["đúng"]


@pytest.mark.parametrize("unsafe_suggestion", ["\ud800", "\ufffe"])
def test_review_parser_drops_suggestions_that_are_not_valid_xml(
    unsafe_suggestion: str,
) -> None:
    target = ReviewSegment("p0@0:12", "document:p0", 0, 0, "Nội dung sai", "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), "")

    discoveries = parse_review_content(
        json.dumps([{"l": 1, "s": "sai", "r": unsafe_suggestion}]), chunk
    )

    assert discoveries == ()


def export_findings(
    text: str,
    *edits: tuple[str, str],
    reason_code: str = "spelling",
) -> list[Finding]:
    """Push raw discoveries through the gate that decides what gets written.

    ``parse_review_content`` no longer narrows a quote down to the minimal edit
    — the compact format has no reason code for it to trust, and the workflow
    has to re-derive one per edit anyway. ``_apply_full_review`` is where a
    broad or invented rewrite is now refused, so that is where these cases are
    asserted.
    """
    block = Block("document:p0", text)
    discoveries = tuple(
        DiscoveryProposal(
            "document:p0",
            text.index(source),
            text.index(source) + len(source),
            source,
            suggestion,
            "spelling",
            reason_code,
            0.99,
        )
        for source, suggestion in edits
    )
    reviewer = Reviewer(FullReviewResult((), discoveries, 1, 1))
    findings, _summary, _failed = _apply_full_review(
        [], [block], reviewer, "", frozenset(), Token()
    )
    return findings



def test_review_export_rejects_unsafe_long_or_semantic_deletions() -> None:
    text = "Nội dung hành chính cần được giữ nguyên trong văn bản."

    assert export_findings(text, (text, ""), reason_code="word_choice") == []


def test_review_export_rejects_a_broad_sentence_with_a_short_replacement() -> None:
    text = (
        "Đề nghị đơn vị ghi chính xác số điện thọai của người tiếp nhận hồ sơ "
        "để thuận tiện liên hệ."
    )

    assert export_findings(text, (text, "điện thoại")) == []


def test_review_export_safely_localizes_one_edit_with_shared_context() -> None:
    text = "Đề nghị ghi số điện thọai để liên hệ."
    corrected = "Đề nghị ghi số điện thoại để liên hệ."

    findings = export_findings(text, (text, corrected))

    assert [(item.source_text, item.suggestion) for item in findings] == [("thọai", "thoại")]
    assert all(text[item.start : item.end] == item.source_text for item in findings)


@pytest.mark.parametrize("reason_code", ["spelling", "compound_word"])
def test_review_export_rejects_a_dissimilar_spelling_replacement(reason_code: str) -> None:
    text = "Kiểm tra ngẩu nhiên hồ sơ."

    assert export_findings(text, ("ngẩu nhiên", "nóng nhiên"), reason_code=reason_code) == []


def test_review_export_rejects_a_guessed_contextual_orthographic_rewrite() -> None:
    text = "Nội dung trể hạng cần sửa."

    assert export_findings(text, ("trể hạng", "tệ hạng"), reason_code="compound_word") == []


@pytest.mark.parametrize(
    ("source_text", "suggestion", "expected"),
    [
        # Vietnamese onset confusions cost two character edits, so the plain
        # distance-1 limit used to reject all of them.
        ("nhất chí", "nhất trí", [("chí", "trí")]),
        ("phong chào", "phong trào", [("chào", "trào")]),
        ("trân trọng", "chân trọng", [("trân", "chân")]),
        ("nàm việc", "làm việc", [("nàm", "làm")]),
        ("dục dịch", "rục rịch", [("dục", "rục"), ("dịch", "rịch")]),
        # "gi" is a digraph onset: its vowel letter belongs to the onset.
        ("dấu diếm", "giấu giếm", [("dấu", "giấu"), ("diếm", "giếm")]),
        ("dành riêng", "giành riêng", [("dành", "giành")]),
    ],
)
def test_localizer_accepts_onset_only_substitutions(
    source_text: str, suggestion: str, expected: list[tuple[str, str]]
) -> None:
    edits = localize_llm_edits(source_text, suggestion, "spelling")
    assert [(item[1], item[2]) for item in edits] == expected
    for offset, edited_source, _ in edits:
        assert source_text[offset : offset + len(edited_source)] == edited_source


@pytest.mark.parametrize(
    ("source_text", "suggestion"),
    [
        # The rime changes too, so these stay guessed rewrites.
        ("xán", "sáng"),
        ("trể", "tệ"),
        # Unrelated wording is never an orthographic fix.
        ("báo cáo", "tài liệu"),
        ("hồ sơ này", "văn bản đó"),
    ],
)
def test_localizer_still_rejects_changes_beyond_the_onset(
    source_text: str, suggestion: str
) -> None:
    assert localize_llm_edits(source_text, suggestion, "spelling") == ()


def test_localizer_rejects_the_same_guessed_minimal_orthographic_rewrite() -> None:
    assert localize_llm_edit("trể", "tệ", "compound_word") is None


def test_localizer_splits_a_clause_rewrite_into_separate_anchored_edits() -> None:
    # Verbatim output from gemma-4-e2b: it ignores the one-error-per-discovery
    # instruction and rewrites the clause, truncated at the schema's 96-char cap.
    source = (
        "Kính gửi uỷ ban nhân dân thành phố hà nội. "
        "Chúng tôi xin bổ xung báo cáo về việc sát nhập hai ph"
    )
    suggestion = (
        "Kính gửi Ủy ban nhân dân thành phố Hà Nội. "
        "Chúng tôi xin bổ sung báo cáo về việc sáp nhập hai ph"
    )
    assert localize_llm_edit(source, suggestion, "spelling") is None

    edits = localize_llm_edits(source, suggestion, "spelling")
    assert [(item[1], item[2]) for item in edits] == [
        ("uỷ", "Ủy"),
        ("hà", "Hà"),
        ("nội", "Nội"),
        ("xung", "sung"),
        ("sát", "sáp"),
    ]
    # Every offset must still anchor exactly inside the supplied source span.
    for offset, edited_source, _ in edits:
        assert source[offset : offset + len(edited_source)] == edited_source


def test_localizer_still_refuses_regions_that_fail_the_per_edit_rules() -> None:
    # A truncated tail leaves an unmatched fragment; it must not become an edit.
    edits = localize_llm_edits(
        "Đơn vị sát nhập hai ph", "Đơn vị sáp nhập hai phòng ban.", "spelling"
    )
    assert [(item[1], item[2]) for item in edits] == [("sát", "sáp")]


def test_review_export_emits_one_finding_per_edit_in_a_clause_rewrite() -> None:
    text = "Kính gửi uỷ ban nhân dân thành phố hà nội."
    rewrite = "Kính gửi Ủy ban nhân dân thành phố Hà Nội."

    findings = export_findings(text, (text, rewrite))

    assert [(item.source_text, item.suggestion) for item in findings] == [
        ("uỷ", "Ủy"),
        ("hà", "Hà"),
        ("nội", "Nội"),
    ]
    assert all(text[item.start : item.end] == item.source_text for item in findings)


def test_low_confidence_discovery_is_kept_because_the_signal_is_uncalibrated(
    tmp_path: Path,
) -> None:
    block = Block("document:p0", "Đơn vị đã bổ xung hồ sơ")
    documents = Documents([block])
    reviewer = Reviewer(
        FullReviewResult(
            (),
            (
                DiscoveryProposal(
                    "document:p0", 10, 17, "bổ xung", "bổ sung", "spelling", "spelling", 0.1
                ),
            ),
            1,
            1,
        )
    )
    processor = ProcessDocument(documents, Dictionary(), RuleEngine(), Reviewers(reviewer))

    result = processor.execute(
        ProcessRequest(
            tmp_path / "source.docx",
            tmp_path / "output.docx",
            Preset.STANDARD,
            use_model=True,
            full_review=True,
        ),
        lambda *_: None,
        Token(),
    )

    assert result.finding_count == 1
    assert [item.source_text for item in documents.written] == ["xung"]
    assert [item.suggestion for item in documents.written] == ["sung"]
    assert [item.confidence for item in documents.written] == [0.1]


def test_review_export_rejects_unicode_normalization_noop() -> None:
    text = "xử"

    assert export_findings(text, (text, unicodedata.normalize("NFD", text))) == []


@pytest.mark.parametrize(
    ("source_text", "suggestion"),
    [("nghiệp vu.", "nghiệp vụ"), ("thòi ", "thời.")],
)
def test_localizer_rejects_mixed_spelling_and_boundary_punctuation_edits(
    source_text: str, suggestion: str
) -> None:
    assert localize_llm_edit(source_text, suggestion, "compound_word") is None


def test_classifier_review_sends_the_compact_contract(tmp_path: Path) -> None:
    runtime = Runtime(json.dumps([{"l": 1, "s": "dang", "r": "đang"}], ensure_ascii=False))
    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {
            "model_id": "test",
            "version": "1",
            "context_size": 4096,
            "review_chunk_tokens": 1200,
        },
        lambda *_: runtime,
    )
    block = Block("document:p0", "Tôi dang làm việc  hôm nay")

    result = classifier.review((block,), (), "", Token())

    assert result.status == "complete"
    assert result.verdicts == ()
    assert [(item.block_id, item.source_text, item.suggestion) for item in result.discoveries] == [
        ("document:p0", "dang", "đang")
    ]
    call = runtime.calls[0]
    assert call["response_format"]["schema"] is REVIEW_SCHEMA
    assert call["repeat_penalty"] == REVIEW_REPEAT_PENALTY
    # The output reserve now follows the chunk, not the context window.
    assert call["max_tokens"] == 1024
    assert call["messages"][1]["content"] == "1|Tôi dang làm việc  hôm nay"
    request_tokens = (
        sum(runtime.count_tokens(message["content"]) for message in call["messages"]) + 32
    )
    assert request_tokens + call["max_tokens"] + 256 <= 4096
    assert call["messages"] in runtime.counted_messages


def test_classifier_output_reserve_never_dwarfs_the_chunk(tmp_path: Path) -> None:
    runtime = Runtime("[]")
    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {
            "model_id": "test",
            "version": "1",
            "max_tokens": 512,
            "review_chunk_tokens": 500,
        },
        lambda *_: runtime,
    )

    result = classifier.review((Block("document:p0", "Nội dung hợp lệ"),), (), "", Token())

    assert result.status == "complete"
    call = runtime.calls[0]
    # A 500-token chunk lands on the 512 floor — not the 4096 a 16k context
    # used to hand every chunk regardless of how little it actually held.
    assert call["max_tokens"] == 512
    assert call["response_format"]["schema"]["type"] == "array"
    assert "verdict" not in call["messages"][0]["content"]


def test_classifier_full_review_fails_when_every_chunk_is_malformed(tmp_path: Path) -> None:
    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {"model_id": "test", "version": "1", "context_size": 2048},
        lambda *_: Runtime("not-json"),
    )
    with pytest.raises(ValueError, match="MODEL_FULL_REVIEW_FAILED"):
        classifier.review((Block("document:p0", "Nội dung"),), (), "", Token())


def test_custom_prompt_overflow_fails_before_document_splitting_or_inference(
    tmp_path: Path,
) -> None:
    runtime = PromptOverflowRuntime(base_tokens=400, custom_tokens=1240)
    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {
            "model_id": "test",
            "version": "1",
            "context_size": 2048,
            "review_chunk_tokens": 500,
        },
        lambda *_: runtime,
    )

    with pytest.raises(ValueError, match="CUSTOM_PROMPT_CONTEXT_EXCEEDED"):
        classifier.review((Block("document:p0", "x"),), (), "rule", Token())

    assert len(runtime.counted_messages) == 2
    assert runtime.calls == []


def test_prompt_budget_verdict_matches_what_the_planner_enforces(tmp_path: Path) -> None:
    """The pre-flight must not disagree with the planner it is warning about.

    A UI that says "fits" for a prompt the planner then rejects reproduces the
    original bug behind a friendlier label, so pin the two together.
    """
    manifest = {
        "model_id": "test",
        "version": "1",
        "context_size": 2048,
        "review_chunk_tokens": 500,
    }
    blocks = (Block("document:p0", "x"),)

    # input_tokens here is 2048 - 512 response - 256 safety = 1280, so a fixed
    # cost above 1216 leaves less than the 64-token floor for document text.
    for base_tokens, custom_tokens, expected_fits in (
        (400, 1100, True),
        (400, 1240, False),
    ):
        runtime = PromptOverflowRuntime(base_tokens=base_tokens, custom_tokens=custom_tokens)
        classifier = LlamaCppClassifier(
            tmp_path / "model.gguf", manifest, lambda *_, _r=runtime: _r
        )

        budget = classifier.prompt_budget("rule")
        assert budget.fits is expected_fits
        assert budget.exact is True
        assert budget.input_tokens == 1280
        assert budget.custom_prompt_tokens == custom_tokens - base_tokens

        if expected_fits:
            # Reaching document tokenization proves the planner cleared the same
            # budget gate the pre-flight just approved.
            with pytest.raises(AssertionError, match="document tokenization should not run"):
                classifier.review(blocks, (), "rule", Token())
        else:
            with pytest.raises(ValueError, match="CUSTOM_PROMPT_CONTEXT_EXCEEDED"):
                classifier.review(blocks, (), "rule", Token())


def test_budget_follows_the_context_the_runtime_actually_loaded(tmp_path: Path) -> None:
    """A shrunk context must shrink the budget, or every request overflows it."""

    class ShrunkRuntime(Runtime):
        def context_size(self) -> int:
            return 4096

    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {
            "model_id": "test",
            "version": "1",
            "context_size": 8192,
        },
        lambda *_: ShrunkRuntime("[]"),
    )

    budget = classifier.prompt_budget("")
    assert budget.context_tokens == 4096
    # 4096 - 700 response - 256 safety.
    assert budget.input_tokens == 3140


def test_prompt_budget_is_estimated_without_loading_the_model() -> None:
    from soatvan.models.review import estimate_prompt_budget

    manifest = {"context_size": 8192, "max_tokens": 512}

    empty = estimate_prompt_budget(manifest, "")
    assert empty.exact is False
    assert empty.fits is True
    assert empty.context_tokens == 8192
    # 8192 - 700 response - 256 safety; the figure the UI shows as the ceiling.
    assert empty.input_tokens == 7236
    assert empty.custom_prompt_tokens == 0

    # With a smaller context, a custom prompt visibly reduces available doc space.
    small_manifest = {"context_size": 2048, "max_tokens": 512}
    small_empty = estimate_prompt_budget(small_manifest, "")
    small_modest = estimate_prompt_budget(small_manifest, "Quy tắc riêng. " * 10)
    assert small_modest.fits is True
    assert small_modest.custom_prompt_tokens > 0
    assert small_modest.document_tokens_available < small_empty.document_tokens_available

    assert estimate_prompt_budget(manifest, "Quy tắc riêng. " * 2000).fits is False


def test_base_review_prompt_overflow_keeps_model_context_error(tmp_path: Path) -> None:
    runtime = PromptOverflowRuntime(base_tokens=1240, custom_tokens=1240)
    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {
            "model_id": "test",
            "version": "1",
            "context_size": 2048,
            "review_chunk_tokens": 500,
        },
        lambda *_: runtime,
    )

    with pytest.raises(ValueError, match="MODEL_REVIEW_CONTEXT_TOO_SMALL"):
        classifier.review((Block("document:p0", "x"),), (), "", Token())

    assert len(runtime.counted_messages) == 1
    assert runtime.calls == []


def test_2463_character_custom_rule_fits_the_local_4096_context(tmp_path: Path) -> None:
    runtime = Runtime("[]")
    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {
            "model_id": "test",
            "version": "1",
            "context_size": 4096,
            "review_chunk_tokens": 500,
        },
        lambda *_: runtime,
    )

    result = classifier.review(
        (Block("document:p0", "Nội dung cần rà soát."),),
        (),
        "q" * 2463,
        Token(),
    )

    assert result.status == "complete"
    assert runtime.calls
    for call in runtime.calls:
        assert call["max_tokens"] == 512
        request_tokens = (
            sum(runtime.count_tokens(message["content"]) for message in call["messages"]) + 32
        )
        assert request_tokens + call["max_tokens"] + 256 <= 4096
        assert call["messages"] in runtime.counted_messages


def test_classifier_rejects_a_context_window_without_review_input_space(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="MODEL_REVIEW_CONTEXT_TOO_SMALL"):
        LlamaCppClassifier(
            tmp_path / "model.gguf",
            {
                "model_id": "test",
                "version": "1",
                "context_size": 512,
                "max_tokens": 512,
            },
            lambda *_: Runtime('{"discoveries":[]}'),
        )
    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {
            "model_id": "test",
            "version": "1",
            "context_size": 1024,
            "max_tokens": 512,
            "review_chunk_tokens": 1200,
        },
        lambda *_: Runtime('{"discoveries":[]}'),
    )
    with pytest.raises(ValueError, match="MODEL_REVIEW_CONTEXT_TOO_SMALL"):
        classifier.review((Block("document:p0", "Nội dung"),), (), "", Token())


def test_classifier_full_review_retries_a_failed_chunk_sequentially(
    tmp_path: Path,
) -> None:
    runtime = PartiallyFailingRuntime('{"discoveries":[]}')
    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {
            "model_id": "test",
            "version": "1",
            "context_size": 2048,
            "review_chunk_tokens": 90,
        },
        lambda *_: runtime,
    )
    result = classifier.review(
        (Block("document:p0", " ".join(f"từ{index}" for index in range(600))),),
        (),
        "",
        Token(),
    )
    assert len(runtime.calls) > 2
    assert result.status == "complete"
    assert result.reviewed_chunks == result.total_chunks
    assert result.failed_chunk_ids == ()
    assert result.retried_chunks == 1
    assert result.recovered_chunks == 1


def test_classifier_reports_failure_reason_after_sequential_retry_is_exhausted(
    tmp_path: Path,
) -> None:
    runtime = RetryStillFailingRuntime('{"discoveries":[]}')
    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {
            "model_id": "test",
            "version": "1",
            "context_size": 2048,
            "review_chunk_tokens": 90,
        },
        lambda *_: runtime,
    )

    result = classifier.review(
        (Block("document:p0", " ".join(f"từ{index}" for index in range(600))),),
        (),
        "",
        Token(),
    )

    assert result.status == "partial"
    assert result.retried_chunks == 7
    assert result.recovered_chunks == 0
    assert result.inference_error_chunks == 1
    assert result.timeout_chunks == 0
    assert result.invalid_output_chunks == 0


def test_review_reports_activity_before_every_sequential_attempt(tmp_path: Path) -> None:
    class AlwaysFailingRuntime(Runtime):
        def create_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            raise RuntimeError("persistent inference failure")

    runtime = AlwaysFailingRuntime('{"discoveries":[]}')
    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {
            "model_id": "test",
            "version": "1",
            "context_size": 4096,
            "review_chunk_tokens": 500,
        },
        lambda *_: runtime,
    )
    progress: list[tuple[int, int]] = []

    with pytest.raises(ValueError, match="MODEL_FULL_REVIEW_FAILED"):
        classifier.review(
            (Block("document:p0", "x" * 200),),
            (),
            "",
            Token(),
            lambda processed, total: progress.append((processed, total)),
        )

    assert len(runtime.calls) == 15
    assert progress == [(0, 1)] * 15 + [(1, 1)]


def test_classifier_recovers_with_a_second_sequential_split_level(
    tmp_path: Path,
) -> None:
    runtime = NestedRetryRuntime('{"discoveries":[]}')
    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {
            "model_id": "test",
            "version": "1",
            "context_size": 2048,
            "review_chunk_tokens": 90,
        },
        lambda *_: runtime,
    )

    result = classifier.review(
        (Block("document:p0", " ".join(f"từ{index}" for index in range(600))),),
        (),
        "",
        Token(),
    )

    assert result.status == "complete"
    assert result.retried_chunks == 2
    assert result.recovered_chunks == 1
    assert result.failed_chunk_ids == ()


class Reviewer:
    version = "model-review@1"
    minimum_confidence = 0.8

    def __init__(self, result: FullReviewResult) -> None:
        self.result = result
        self.review_candidates = ()

    def classify(self, candidates, custom_prompt, cancellation):
        del candidates, custom_prompt, cancellation
        return ()

    def review(self, blocks, candidates, custom_prompt, cancellation, progress=None):
        del blocks, custom_prompt
        self.review_candidates = candidates
        cancellation.raise_if_cancelled()
        if progress:
            progress(self.result.total_chunks, self.result.total_chunks)
        return self.result


class Reviewers:
    def __init__(self, reviewer: Reviewer) -> None:
        self._reviewer = reviewer

    def classifier(self):
        return self._reviewer

    def supports_full_review(self) -> bool:
        return True


class UnapprovedReviewers(Reviewers):
    def supports_full_review(self) -> bool:
        return False


class ExplodingRules:
    def check(self, *_args, **_kwargs):
        raise AssertionError("LLM-only full review must not execute deterministic rules")


def test_workflow_full_review_finds_error_without_rule_candidate(tmp_path: Path) -> None:
    documents = Documents([Block("document:p0", "Tôi dang làm việc")])
    discovery = DiscoveryProposal("document:p0", 4, 8, "dang", "đang", "spelling", "spelling", 0.96)
    reviewer = Reviewer(FullReviewResult((), (discovery,), 1, 1))
    processor = ProcessDocument(documents, Dictionary(), ExplodingRules(), Reviewers(reviewer))
    result = processor.execute(
        ProcessRequest(
            tmp_path / "source.docx",
            tmp_path / "output.docx",
            Preset.SPELLING,
            use_model=True,
            full_review=True,
        ),
        lambda *_: None,
        Token(),
    )
    assert result.finding_count == 1
    assert result.counts == {"category": {"spelling": 1}, "origin": {"llm": 1}}
    assert result.review == {
        "status": "complete",
        "total_chunks": 1,
        "reviewed_chunks": 1,
        "failed_chunks": 0,
        "total_blocks": 1,
        "reviewed_blocks": 1,
        "failed_blocks": 0,
        "timeout_chunks": 0,
        "invalid_output_chunks": 0,
        "inference_error_chunks": 0,
        "retried_chunks": 0,
        "recovered_chunks": 0,
    }
    assert documents.written[0].source_text == "dang"
    assert documents.written[0].detector_id == "llm.discovery.diacritic.v2"
    assert documents.written[0].reason == "Dấu tiếng Việt có thể được đặt chưa đúng."


def test_workflow_full_review_optionally_includes_deterministic_findings(
    tmp_path: Path,
) -> None:
    block = Block("document:p0", "Đơn vị sát nhập hồ sơ")
    documents = Documents([block])
    rule_finding = RuleEngine().check([block], Preset.STANDARD)[0]
    reviewer = Reviewer(
        FullReviewResult((ClassifierVerdict(rule_finding.id, "keep", 0.95),), (), 1, 1)
    )
    processor = ProcessDocument(documents, Dictionary(), RuleEngine(), Reviewers(reviewer))
    progress: list[tuple[str, int, str]] = []

    result = processor.execute(
        ProcessRequest(
            tmp_path / "source.docx",
            tmp_path / "output.docx",
            Preset.STANDARD,
            use_model=True,
            full_review=True,
            include_rule_findings=True,
        ),
        lambda *item: progress.append(item),
        Token(),
    )

    # LLM runs independently — no candidates from rules/seq2seq.
    assert list(reviewer.review_candidates) == []
    assert [(item.source_text, item.origin) for item in documents.written] == [("sát nhập", "rule")]
    # Rule finding kept as-is, not modified by LLM verdict.
    assert documents.written[0].rule_version == "rules-0.2.0"
    assert result.counts == {"category": {"spelling": 1}, "origin": {"rule": 1}}
    assert ("rules", 35, "job.applying_rules") in progress


def test_full_review_confident_drop_never_removes_optional_rule_finding(
    tmp_path: Path,
) -> None:
    block = Block("document:p0", "Đơn vị sát nhập hồ sơ")
    documents = Documents([block])
    rule_finding = RuleEngine().check([block], Preset.STANDARD)[0]
    reviewer = Reviewer(
        FullReviewResult((ClassifierVerdict(rule_finding.id, "drop", 0.95),), (), 1, 1)
    )
    processor = ProcessDocument(documents, Dictionary(), RuleEngine(), Reviewers(reviewer))

    result = processor.execute(
        ProcessRequest(
            tmp_path / "source.docx",
            tmp_path / "output.docx",
            Preset.STANDARD,
            use_model=True,
            full_review=True,
            include_rule_findings=True,
        ),
        lambda *_: None,
        Token(),
    )

    # Self-reported confidence is not calibrated, so a confident `drop` is not
    # evidence that a deterministic detector was wrong. The finding is kept.
    assert result.finding_count == 1
    assert result.output_path is not None
    assert [item.source_text for item in documents.written] == ["sát nhập"]
    assert [item.suggestion for item in documents.written] == ["sáp nhập"]
    assert [item.origin for item in documents.written] == ["rule"]


@pytest.mark.parametrize(
    ("use_model", "full_review"),
    [(False, False), (True, False), (False, True)],
)
def test_rule_findings_option_is_scoped_to_ai_full_review(
    tmp_path: Path, use_model: bool, full_review: bool
) -> None:
    processor = ProcessDocument(
        Documents([Block("document:p0", "sát nhập")]),
        Dictionary(),
        RuleEngine(),
    )

    with pytest.raises(ValueError, match="INCLUDE_RULE_FINDINGS_REQUIRES_FULL_REVIEW"):
        processor.execute(
            ProcessRequest(
                tmp_path / "source.docx",
                tmp_path / "output.docx",
                Preset.STANDARD,
                use_model=use_model,
                full_review=full_review,
                include_rule_findings=True,
            ),
            lambda *_: None,
            Token(),
        )


def test_workflow_rejects_full_review_without_an_approved_capability(
    tmp_path: Path,
) -> None:
    documents = Documents([Block("document:p0", "N\u1ed9i dung")])
    reviewer = Reviewer(FullReviewResult((), (), 1, 1))
    processor = ProcessDocument(
        documents, Dictionary(), RuleEngine(), UnapprovedReviewers(reviewer)
    )

    with pytest.raises(ValueError, match="MODEL_FULL_REVIEW_NOT_APPROVED"):
        processor.execute(
            ProcessRequest(
                tmp_path / "source.docx",
                tmp_path / "output.docx",
                Preset.SPELLING,
                use_model=True,
                full_review=True,
            ),
            lambda *_: None,
            Token(),
        )


def test_partial_llm_only_review_does_not_fall_back_to_rules(tmp_path: Path) -> None:
    documents = Documents(
        [Block("document:p0", "sát nhập nội dung"), Block("document:p1", "Đoạn khác")]
    )
    reviewer = Reviewer(
        FullReviewResult(
            (ClassifierVerdict("unknown", "drop", 1),),
            (),
            2,
            1,
            ("chunk-1",),
            ("document:p0",),
        )
    )
    processor = ProcessDocument(documents, Dictionary(), ExplodingRules(), Reviewers(reviewer))
    result = processor.execute(
        ProcessRequest(
            tmp_path / "source.docx",
            tmp_path / "output.docx",
            Preset.STANDARD,
            use_model=True,
            full_review=True,
        ),
        lambda *_: None,
        Token(),
    )
    assert result.finding_count == 0
    assert result.output_path is None
    assert result.counts == {}
    assert result.review and result.review["status"] == "partial"


def test_full_review_marks_blocks_with_unexportable_findings_as_partial(
    tmp_path: Path,
) -> None:
    blocks = [
        Block("document:p0", "first bad text"),
        Block("document:p1", "second bad text"),
    ]
    documents = PartiallyWritableDocuments(blocks, {"document:p0"})
    reviewer = Reviewer(
        FullReviewResult(
            (),
            (
                DiscoveryProposal("document:p0", 6, 9, "bad", "bed", "spelling", "spelling", 0.99),
                DiscoveryProposal("document:p1", 7, 10, "bad", "bed", "spelling", "spelling", 0.99),
            ),
            1,
            1,
        )
    )
    processor = ProcessDocument(documents, Dictionary(), RuleEngine(), Reviewers(reviewer))

    result = processor.execute(
        ProcessRequest(
            tmp_path / "source.docx",
            tmp_path / "output.docx",
            Preset.SPELLING,
            use_model=True,
            full_review=True,
        ),
        lambda *_: None,
        Token(),
    )

    assert result.finding_count == 1
    assert result.review == {
        "status": "partial",
        "total_chunks": 1,
        "reviewed_chunks": 1,
        "failed_chunks": 0,
        "total_blocks": 2,
        "reviewed_blocks": 1,
        "failed_blocks": 1,
        "timeout_chunks": 0,
        "invalid_output_chunks": 0,
        "inference_error_chunks": 0,
        "retried_chunks": 0,
        "recovered_chunks": 0,
    }


def test_discovery_bridge_does_not_remove_two_non_overlapping_rule_findings() -> None:
    def finding(finding_id: str, start: int, end: int, detector: str, confidence: float) -> Finding:
        return Finding(
            finding_id,
            "spelling",
            "llm" if detector.startswith("llm.discovery.") else "rule",
            detector,
            "document:p0",
            start,
            end,
            "abcdef"[start:end],
            "x",
            "Lý do",
            "v1",
            confidence,
        )

    left = finding("left", 0, 2, "rule.left", 0.9)
    bridge = finding("bridge", 1, 5, "llm.discovery.grammar.v2", 1.0)
    right = finding("right", 4, 6, "rule.right", 0.9)
    merged = _merge_review_findings([left, bridge, right], [Block("document:p0", "abcdef")])
    assert [item.id for item in merged] == ["left", "right"]
    limited = _limit_review_findings(merged, [], 2)
    assert {item.id for item in limited} == {"left", "right"}


def test_overlapping_ai_discoveries_prefer_the_shorter_anchor() -> None:
    block = Block("document:p0", "abcdef")

    def discovery(finding_id: str, start: int, end: int, confidence: float) -> Finding:
        return Finding(
            finding_id,
            "spelling",
            "llm",
            "llm.discovery.spelling.v2",
            block.id,
            start,
            end,
            block.text[start:end],
            "x",
            "Lý do",
            "model@1",
            confidence,
        )

    broad = discovery("broad", 0, 6, 0.99)
    narrow = discovery("narrow", 2, 4, 0.90)

    assert [item.id for item in _merge_review_findings([broad, narrow], [block])] == ["narrow"]


def test_ai_discovery_wins_when_its_conflicting_anchor_is_not_broader() -> None:
    block = Block("document:p0", "abcdef")

    def finding(
        finding_id: str,
        start: int,
        end: int,
        detector: str,
        suggestion: str,
    ) -> Finding:
        return Finding(
            finding_id,
            "spelling",
            "llm" if detector.startswith("llm.discovery.") else "rule",
            detector,
            block.id,
            start,
            end,
            block.text[start:end],
            suggestion,
            "Lý do",
            "v2",
            0.9,
        )

    broad_rule = finding("broad-rule", 0, 4, "rule.broad", "x")
    narrow_ai = finding("narrow-ai", 1, 3, "llm.discovery.spelling.v2", "y")
    assert [item.id for item in _merge_review_findings([broad_rule, narrow_ai], [block])] == [
        "narrow-ai"
    ]

    equal_rule = finding("equal-rule", 4, 6, "rule.equal", "x")
    equal_ai = finding("equal-ai", 4, 6, "llm.discovery.spelling.v2", "y")
    assert [item.id for item in _merge_review_findings([equal_rule, equal_ai], [block])] == [
        "equal-ai"
    ]


def test_review_limit_reserves_quota_for_ai_discoveries() -> None:
    block = Block("document:p0", "abcdef")

    def finding(finding_id: str, start: int, detector: str) -> Finding:
        return Finding(
            finding_id,
            "spelling",
            "llm" if detector.startswith("llm.discovery.") else "rule",
            detector,
            block.id,
            start,
            start + 1,
            block.text[start : start + 1],
            "x",
            "Lý do",
            "v2",
            0.9,
        )

    rule_left = finding("rule-left", 0, "rule.left")
    rule_right = finding("rule-right", 2, "rule.right")
    ai = finding("ai", 4, "llm.discovery.spelling.v2")

    limited = _limit_review_findings([rule_left, rule_right, ai], [block], 2)
    assert {item.id for item in limited} == {"ai", "rule-left"}


# ---------------------------------------------------------------------------
# Lightweight review mode tests
# ---------------------------------------------------------------------------


def test_review_output_reserve_is_derived_from_the_chunk_not_the_context() -> None:
    small = review_budget_from_manifest(
        {"context_size": 4096, "review_chunk_tokens": 500}
    )
    default = review_budget_from_manifest({"context_size": 16384})
    large = review_budget_from_manifest(
        {"context_size": 16384, "review_chunk_tokens": 8000}
    )

    # A tiny chunk gets the floor, not a share of the window.
    assert small.review_output_tokens == MIN_REVIEW_OUTPUT_TOKENS
    # No manifest value means the default target chunk, and half of it.
    assert default.budget.document_tokens == DEFAULT_REVIEW_CHUNK_TOKENS
    assert default.review_output_tokens == DEFAULT_REVIEW_CHUNK_TOKENS
    # A large chunk stops at the ceiling instead of scaling without bound.
    assert large.review_output_tokens == MAX_REVIEW_OUTPUT_TOKENS
    # Growing the context alone must never grow the output reserve again.
    assert (
        review_budget_from_manifest({"context_size": 32768, "review_chunk_tokens": 500})
        .review_output_tokens
        == MIN_REVIEW_OUTPUT_TOKENS
    )


