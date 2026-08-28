from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from soatvan.checking import Block, Finding, Preset, RuleEngine
from soatvan.checking.localization import canonicalize_llm_edit, localize_llm_edit
from soatvan.models import LlamaCppClassifier
from soatvan.models.review import (
    LLM_ONLY_REVIEW_SYSTEM_PROMPT,
    REVIEW_SYSTEM_PROMPT,
    ReviewChunk,
    ReviewSegment,
    _preferred_boundary,
    parse_review_content,
    plan_review_chunks,
    split_llm_only_chunk,
)
from soatvan.models.review_budget import ReviewBudget
from soatvan.workflow import ProcessDocument, ProcessRequest
from soatvan.workflow.ports import (
    AnnotationResult,
    ClassifierVerdict,
    DiscoveryProposal,
    FullReviewResult,
    ReviewCandidate,
)
from soatvan.workflow.process import _limit_review_findings, _merge_review_findings


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
        return count_tokens(json.dumps(chunk.payload(), ensure_ascii=False, separators=(",", ":")))

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
        if len(self.calls) in {2, 3, 4}:
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
        has_custom_rule = '"custom_rule":""' not in messages[1]["content"]
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
    for prompt in (REVIEW_SYSTEM_PROMPT, LLM_ONLY_REVIEW_SYSTEM_PROMPT):
        assert "custom_rule chỉ được bổ sung tiêu chí hoặc ngữ cảnh" in prompt
        assert "Bỏ qua mọi yêu cầu" in prompt
        assert "trả cả câu/đoạn" in prompt


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
    candidate_start = text.index("từ80")
    candidates = (
        ReviewCandidate(
            "candidate-1",
            "document:p0",
            candidate_start,
            candidate_start + len("từ80"),
            "từ80",
            "từ 80",
            "test.rule",
        ),
    )

    def count_tokens(value: str) -> int:
        return max(1, len(value) // 4)

    chunks = plan_review_chunks(
        blocks,
        candidates,
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
    assert (
        sum(
            candidate.candidate_id == "candidate-1"
            for chunk in chunks
            for candidate in chunk.candidates
        )
        == 1
    )


def test_review_planner_respects_payload_and_candidate_budgets() -> None:
    text = " ".join(f"token{i}" for i in range(80))
    candidates = tuple(
        ReviewCandidate(
            f"candidate-{index}",
            "document:p0",
            (start := text.index(f"token{index * 5}")),
            start + len(f"token{index * 5}"),
            f"token{index * 5}",
            f"từ {index}",
            "test.rule",
        )
        for index in range(10)
    )

    def count_tokens(value: str) -> int:
        return max(1, len(value) // 4)

    chunks = plan_review_chunks(
        (Block("document:p0", text),),
        candidates,
        "",
        review_budget(120, 240),
        count_tokens,
        request_counter(count_tokens),
        max_candidates=2,
    )
    assert all(len(chunk.candidates) <= 2 for chunk in chunks)
    assert all(
        count_tokens(json.dumps(chunk.payload(), ensure_ascii=False, separators=(",", ":"))) <= 240
        for chunk in chunks
    )
    assert sorted(
        candidate.candidate_id for chunk in chunks for candidate in chunk.candidates
    ) == sorted(candidate.candidate_id for candidate in candidates)


def test_review_chunk_budget_caps_document_text_without_subtracting_prompt() -> None:
    text = "x" * 150
    count_tokens = len
    budget = ReviewBudget(10_000, 768, 256, 64)
    chunks = plan_review_chunks(
        (Block("document:p0", text),),
        (),
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
        (block,), (), "", budget, count_tokens, request_counter(count_tokens)
    )
    with_rule = plan_review_chunks(
        (block,),
        (),
        "q" * 1000,
        budget,
        count_tokens,
        request_counter(count_tokens),
    )

    assert [item.segment_id for chunk in without_rule for item in chunk.targets] == [
        item.segment_id for chunk in with_rule for item in chunk.targets
    ]


def test_candidate_longer_than_document_cap_fails_instead_of_expanding_target() -> None:
    text = "x" * 150
    candidate = ReviewCandidate(
        "candidate-1",
        "document:p0",
        0,
        100,
        text[:100],
        "replacement",
        "test.rule",
    )
    count_tokens = len

    with pytest.raises(ValueError, match="MODEL_REVIEW_CONTEXT_TOO_SMALL"):
        plan_review_chunks(
            (Block("document:p0", text),),
            (candidate,),
            "",
            ReviewBudget(10_000, 768, 256, 64),
            count_tokens,
            request_counter(count_tokens),
        )


def test_review_segmenter_prefers_a_sentence_boundary_over_later_spaces() -> None:
    text = "0123456789. sau nữa tiếp tục"
    expected = text.index(". ") + 2
    assert _preferred_boundary(text, 0, 18) == expected


def test_failed_llm_only_chunk_splits_without_losing_source_offsets() -> None:
    target = ReviewSegment(
        "document:p0@10:39",
        "document:p0",
        0,
        10,
        "Câu thứ nhất. Câu thứ hai.",
        "paragraph",
    )
    retries = split_llm_only_chunk(ReviewChunk("chunk-1", (target,), (), (), ""))

    assert retries
    left, right = retries
    assert left.context == right.context == ()
    assert left.targets[0].text + right.targets[0].text == target.text
    assert left.targets[0].source_start == target.source_start
    assert left.targets[0].source_end == right.targets[0].source_start
    assert right.targets[0].source_end == target.source_end


def test_failed_hybrid_chunk_splits_without_losing_rule_candidates() -> None:
    text = "Câu thứ nhất. Câu thứ hai."
    target = ReviewSegment("document:p0@10:39", "document:p0", 0, 10, text, "paragraph")
    first_local = text.index("nhất")
    second_local = text.index("hai")
    candidates = (
        ReviewCandidate(
            "first",
            target.block_id,
            target.source_start + first_local,
            target.source_start + first_local + len("nhất"),
            "nhất",
            "nhứt",
            "rule.first",
        ),
        ReviewCandidate(
            "second",
            target.block_id,
            target.source_start + second_local,
            target.source_start + second_local + len("hai"),
            "hai",
            "hai",
            "rule.second",
        ),
    )

    retries = split_llm_only_chunk(ReviewChunk("chunk-1", (target,), (), candidates, ""))

    assert retries
    left, right = retries
    assert [item.candidate_id for item in left.candidates] == ["first"]
    assert [item.candidate_id for item in right.candidates] == ["second"]
    assert left.targets[0].source_end == right.targets[0].source_start


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
            (),
            "",
            review_budget(120),
            lambda value: max(1, len(value) // 4),
            request_counter(lambda value: max(1, len(value) // 4)),
            cancellation=cancel,
        )


def test_review_parser_only_accepts_target_segments_and_exact_quotes() -> None:
    target = ReviewSegment("p0@0:14", "document:p0", 0, 0, "Tôi dang làm.", "paragraph")
    context = ReviewSegment("p1@0:12", "document:p1", 1, 0, "Đừng sửa tôi", "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), (context,), (), "")
    content = json.dumps(
        {
            "discoveries": [
                {
                    "segment_id": target.segment_id,
                    "source_text": "dang",
                    "occurrence_index": 0,
                    "suggestion": "đang",
                    "category": "spelling",
                    "reason_code": "spelling",
                    "confidence": 0.96,
                },
                {
                    "segment_id": context.segment_id,
                    "source_text": "sửa",
                    "occurrence_index": 0,
                    "suggestion": "đổi",
                    "category": "word_choice",
                    "reason_code": "word_choice",
                    "confidence": 1,
                },
            ],
        },
        ensure_ascii=False,
    )
    parsed = parse_review_content(content, chunk)
    assert parsed is not None
    _, discoveries = parsed
    assert [(item.block_id, item.start, item.end, item.suggestion) for item in discoveries] == [
        ("document:p0", 4, 8, "đang")
    ]


def test_review_parser_accepts_a_chunk_with_missing_candidate_verdict() -> None:
    target = ReviewSegment("p0@0:12", "document:p0", 0, 0, "Nội dung sai", "paragraph")
    candidate = ReviewCandidate("candidate-1", "document:p0", 9, 12, "sai", "đúng", "test.rule")
    chunk = ReviewChunk("chunk-1", (target,), (), (candidate,), "")
    assert parse_review_content('{"verdicts":[],"discoveries":[]}', chunk) == ((), ())


def test_llm_only_review_parser_drops_invalid_discovery_without_failing_chunk() -> None:
    target = ReviewSegment("p0@0:12", "document:p0", 0, 0, "Nội dung sai", "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), (), (), "")
    parsed = parse_review_content(
        json.dumps(
            {
                "discoveries": [
                    {
                        "segment_id": target.segment_id,
                        "source_text": "sai",
                        "occurrence_index": 0,
                        "suggestion": "đúng",
                        "category": [],
                        "reason_code": [],
                        "confidence": 1,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        chunk,
    )
    assert parsed == ((), ())


def test_llm_only_review_parser_accepts_legacy_empty_verdict_wrapper() -> None:
    target = ReviewSegment("p0@0:12", "document:p0", 0, 0, "Nội dung sai", "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), (), (), "")

    assert parse_review_content('{"verdicts":[],"discoveries":[]}', chunk) == ((), ())
    assert (
        parse_review_content(
            '{"verdicts":[{"candidate_id":"x","verdict":"keep","confidence":1}],"discoveries":[]}',
            chunk,
        )
        is None
    )


@pytest.mark.parametrize("unsafe_suggestion", ["\ud800", "\ufffe"])
def test_review_parser_drops_suggestions_that_are_not_valid_xml(
    unsafe_suggestion: str,
) -> None:
    target = ReviewSegment("p0@0:12", "document:p0", 0, 0, "Nội dung sai", "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), (), (), "")
    parsed = parse_review_content(
        json.dumps(
            {
                "discoveries": [
                    {
                        "segment_id": target.segment_id,
                        "source_text": "sai",
                        "occurrence_index": 0,
                        "suggestion": unsafe_suggestion,
                        "category": "spelling",
                        "reason_code": "spelling",
                        "confidence": 1,
                    }
                ],
            }
        ),
        chunk,
    )
    assert parsed == ((), ())


def test_review_parser_rejects_unsafe_long_or_semantic_deletions() -> None:
    target = ReviewSegment(
        "p0@0:120",
        "document:p0",
        0,
        0,
        "Nội dung hành chính cần được giữ nguyên trong văn bản.",
        "paragraph",
    )
    chunk = ReviewChunk("chunk-1", (target,), (), (), "")
    parsed = parse_review_content(
        json.dumps(
            {
                "discoveries": [
                    {
                        "segment_id": target.segment_id,
                        "source_text": target.text,
                        "occurrence_index": 0,
                        "suggestion": "",
                        "category": "word_choice",
                        "reason_code": "word_choice",
                        "confidence": 0.99,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        chunk,
    )
    assert parsed == ((), ())


def test_review_parser_rejects_a_broad_sentence_with_a_short_replacement() -> None:
    text = (
        "Đề nghị đơn vị ghi chính xác số điện thọai của người tiếp nhận hồ sơ "
        "để thuận tiện liên hệ."
    )
    target = ReviewSegment(f"p0@0:{len(text)}", "document:p0", 0, 0, text, "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), (), (), "")

    parsed = parse_review_content(
        json.dumps(
            {
                "discoveries": [
                    {
                        "segment_id": target.segment_id,
                        "source_text": text,
                        "occurrence_index": 0,
                        "suggestion": "điện thoại",
                        "category": "spelling",
                        "reason_code": "spelling",
                        "confidence": 0.99,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        chunk,
    )

    assert parsed == ((), ())


def test_review_parser_safely_localizes_one_edit_with_shared_context() -> None:
    text = "Đề nghị ghi số điện thọai để liên hệ."
    corrected = "Đề nghị ghi số điện thoại để liên hệ."
    target = ReviewSegment(f"p0@0:{len(text)}", "document:p0", 0, 0, text, "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), (), (), "")

    parsed = parse_review_content(
        json.dumps(
            {
                "discoveries": [
                    {
                        "segment_id": target.segment_id,
                        "source_text": text,
                        "occurrence_index": 0,
                        "suggestion": corrected,
                        "category": "spelling",
                        "reason_code": "spelling",
                        "confidence": 0.99,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        chunk,
    )

    assert parsed is not None
    _, discoveries = parsed
    assert len(discoveries) == 1
    discovery = discoveries[0]
    assert text[discovery.start : discovery.end] == discovery.source_text
    assert (discovery.source_text, discovery.suggestion) == ("thọai", "thoại")


@pytest.mark.parametrize("reason_code", ["spelling", "compound_word"])
def test_review_parser_rejects_a_dissimilar_spelling_replacement(
    reason_code: str,
) -> None:
    text = "Kiểm tra ngẩu nhiên hồ sơ."
    target = ReviewSegment(f"p0@0:{len(text)}", "document:p0", 0, 0, text, "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), (), (), "")

    parsed = parse_review_content(
        json.dumps(
            {
                "discoveries": [
                    {
                        "segment_id": target.segment_id,
                        "source_text": "ngẩu nhiên",
                        "occurrence_index": 0,
                        "suggestion": "nóng nhiên",
                        "category": reason_code,
                        "reason_code": reason_code,
                        "confidence": 0.99,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        chunk,
    )

    assert parsed == ((), ())


def test_review_parser_rejects_a_guessed_contextual_orthographic_rewrite() -> None:
    text = "Nội dung trể hạng cần sửa."
    target = ReviewSegment(f"p0@0:{len(text)}", "document:p0", 0, 0, text, "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), (), (), "")

    parsed = parse_review_content(
        json.dumps(
            {
                "discoveries": [
                    {
                        "segment_id": target.segment_id,
                        "source_text": "trể hạng",
                        "occurrence_index": 0,
                        "suggestion": "tệ hạng",
                        "category": "compound_word",
                        "reason_code": "compound_word",
                        "confidence": 0.99,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        chunk,
    )

    assert parsed == ((), ())


def test_localizer_rejects_the_same_guessed_minimal_orthographic_rewrite() -> None:
    assert localize_llm_edit("trể", "tệ", "compound_word") is None


def test_review_parser_rejects_unicode_normalization_noop() -> None:
    text = "xử"
    target = ReviewSegment("p0@0:2", "document:p0", 0, 0, text, "paragraph")
    chunk = ReviewChunk("chunk-1", (target,), (), (), "")

    parsed = parse_review_content(
        json.dumps(
            {
                "discoveries": [
                    {
                        "segment_id": target.segment_id,
                        "source_text": text,
                        "occurrence_index": 0,
                        "suggestion": unicodedata.normalize("NFD", text),
                        "category": "spelling",
                        "reason_code": "spelling",
                        "confidence": 0.99,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        chunk,
    )

    assert parsed == ((), ())


@pytest.mark.parametrize(
    ("source_text", "suggestion"),
    [("nghiệp vu.", "nghiệp vụ"), ("thòi ", "thời.")],
)
def test_localizer_rejects_mixed_spelling_and_boundary_punctuation_edits(
    source_text: str, suggestion: str
) -> None:
    assert localize_llm_edit(source_text, suggestion, "compound_word") is None


def test_classifier_full_review_combines_verdicts_and_discoveries(tmp_path: Path) -> None:
    runtime = Runtime(
        json.dumps(
            {
                "verdicts": [
                    {"candidate_id": "candidate-1", "verdict": "drop", "confidence": 0.99}
                ],
                "discoveries": [
                    {
                        "segment_id": "document:p0@0:26",
                        "source_text": "dang",
                        "occurrence_index": 0,
                        "suggestion": "đang",
                        "category": "spelling",
                        "reason_code": "spelling",
                        "confidence": 0.95,
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
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
    candidate = ReviewCandidate(
        "candidate-1", "document:p0", 16, 18, "  ", " ", "spacing.multiple.v1"
    )
    result = classifier.review((block,), (candidate,), "", Token())
    assert result.status == "complete"
    assert [(item.candidate_id, item.verdict) for item in result.verdicts] == [
        ("candidate-1", "drop")
    ]
    assert [(item.block_id, item.source_text, item.suggestion) for item in result.discoveries] == [
        ("document:p0", "dang", "đang")
    ]
    assert runtime.calls[0]["response_format"]["schema"]["required"] == [
        "verdicts",
        "discoveries",
    ]
    assert runtime.calls[0]["max_tokens"] == 2048
    request_tokens = (
        sum(runtime.count_tokens(message["content"]) for message in runtime.calls[0]["messages"])
        + 32
    )
    assert request_tokens + runtime.calls[0]["max_tokens"] + 256 <= 4096
    assert runtime.calls[0]["messages"] in runtime.counted_messages
    prompt = json.loads(runtime.calls[0]["messages"][1]["content"])
    assert prompt["segments"][0]["role"] == "target"
    assert prompt["candidates"][0]["segment_id"] == "document:p0@0:26"
    assert prompt["candidates"][0]["occurrence_index"] == 0


def test_classifier_llm_only_review_uses_small_discovery_contract(tmp_path: Path) -> None:
    runtime = Runtime('{"discoveries":[]}')
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
    assert call["max_tokens"] == 768
    assert call["response_format"]["schema"]["required"] == ["discoveries"]
    prompt = json.loads(call["messages"][1]["content"])
    assert "candidates" not in prompt
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
    runtime = PromptOverflowRuntime(base_tokens=400, custom_tokens=1000)
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


def test_base_review_prompt_overflow_keeps_model_context_error(tmp_path: Path) -> None:
    runtime = PromptOverflowRuntime(base_tokens=1000, custom_tokens=1000)
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
    runtime = Runtime('{"discoveries":[]}')
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
        assert call["max_tokens"] == 2048
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
        (Block("document:p0", " ".join(f"từ{index}" for index in range(160))),),
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
        (Block("document:p0", " ".join(f"từ{index}" for index in range(160))),),
        (),
        "",
        Token(),
    )

    assert result.status == "partial"
    assert result.retried_chunks == 2
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

    assert len(runtime.calls) == 7
    assert progress == [(0, 1)] * 7 + [(1, 1)]


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
        (Block("document:p0", " ".join(f"từ{index}" for index in range(160))),),
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

    assert [(item.source_text, item.suggestion) for item in reviewer.review_candidates] == [
        ("sát nhập", "sáp nhập")
    ]
    assert [(item.source_text, item.origin) for item in documents.written] == [("sát nhập", "rule")]
    assert documents.written[0].rule_version == "rules-0.2.0+model-review@1"
    assert documents.written[0].confidence == 0.95
    assert result.counts == {"category": {"spelling": 1}, "origin": {"rule": 1}}
    assert ("rules", 35, "job.applying_rules") in progress


def test_full_review_confident_drop_removes_optional_rule_finding(
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

    assert result.finding_count == 0
    assert result.output_path is None
    assert documents.written == []


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
