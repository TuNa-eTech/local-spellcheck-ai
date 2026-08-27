from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from soatvan.checking import Block, Finding, Preset, RuleEngine
from soatvan.models import LlamaCppClassifier
from soatvan.models.review import (
    ReviewChunk,
    ReviewSegment,
    _preferred_boundary,
    parse_review_content,
    plan_review_chunks,
    split_llm_only_chunk,
)
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

    def count_tokens(self, value: str) -> int:
        return max(1, len(value) // 4)

    def create_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"choices": [{"message": {"content": self.content}}]}


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
        self.written = [
            item for item in findings if item.block_id in self.writable_block_ids
        ]
        return AnnotationResult(tuple(item.id for item in self.written))


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
    chunks = plan_review_chunks(
        blocks, candidates, "", 90, lambda value: max(1, len(value) // 4)
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
    assert sum(candidate.candidate_id == "candidate-1" for chunk in chunks for candidate in chunk.candidates) == 1


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
        120,
        count_tokens,
        max_candidates=2,
    )
    assert all(len(chunk.candidates) <= 2 for chunk in chunks)
    assert all(
        count_tokens(
            json.dumps(chunk.payload(), ensure_ascii=False, separators=(",", ":"))
        )
        <= 120
        for chunk in chunks
    )
    assert sorted(
        candidate.candidate_id for chunk in chunks for candidate in chunk.candidates
    ) == sorted(candidate.candidate_id for candidate in candidates)


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
            120,
            lambda value: max(1, len(value) // 4),
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
    candidate = ReviewCandidate(
        "candidate-1", "document:p0", 9, 12, "sai", "đúng", "test.rule"
    )
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
    assert parse_review_content(
        '{"verdicts":[{"candidate_id":"x","verdict":"keep","confidence":1}],"discoveries":[]}',
        chunk,
    ) is None


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
    with pytest.raises(ValueError, match="MODEL_REVIEW_CONTEXT_TOO_SMALL"):
        LlamaCppClassifier(
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

    def classify(self, candidates, custom_prompt, cancellation):
        del candidates, custom_prompt, cancellation
        return ()

    def review(self, blocks, candidates, custom_prompt, cancellation, progress=None):
        del blocks, candidates, custom_prompt
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
    discovery = DiscoveryProposal(
        "document:p0", 4, 8, "dang", "đang", "spelling", "spelling", 0.96
    )
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
                DiscoveryProposal(
                    "document:p0", 6, 9, "bad", "good", "spelling", "spelling", 0.99
                ),
                DiscoveryProposal(
                    "document:p1", 7, 10, "bad", "good", "spelling", "spelling", 0.99
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
    def finding(
        finding_id: str, start: int, end: int, detector: str, confidence: float
    ) -> Finding:
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
    bridge = finding("bridge", 1, 5, "llm.discovery.grammar.v1", 1.0)
    right = finding("right", 4, 6, "rule.right", 0.9)
    merged = _merge_review_findings(
        [left, bridge, right], [Block("document:p0", "abcdef")]
    )
    assert [item.id for item in merged] == ["left", "right"]
    limited = _limit_review_findings([bridge, left, right], [], 2)
    assert {item.id for item in limited} == {"left", "right"}
