from __future__ import annotations

from pathlib import Path

import pytest

from soatvan.checking import Block, Preset, RuleConfig, RuleEngine
from soatvan.workflow import ProcessDocument, ProcessRequest
from soatvan.workflow.ports import AnnotationResult, ClassifierVerdict


class Dictionary:
    def ignored_words(self) -> frozenset[str]:
        return frozenset()


class Documents:
    def read_blocks(self, _: Path) -> list[Block]:
        return [Block("document:p0", "sát nhập  nội dung")]

    def inspect(self, _: Path) -> dict[str, object]:
        return {}

    def write_annotations(
        self, source: Path, target: Path, findings, cancellation=None
    ) -> AnnotationResult:
        del source, target, cancellation
        # Simulate one stale anchor being rejected by the OOXML adapter.
        return AnnotationResult((findings[0].id,))


class Token:
    def raise_if_cancelled(self) -> None:
        return None


class Classifier:
    version = "model-test@1"
    minimum_confidence = 0.8

    def classify(self, candidates, custom_prompt, cancellation):
        del custom_prompt
        cancellation.raise_if_cancelled()
        return tuple(
            ClassifierVerdict(item.candidate_id, "keep", 0.95)
            for item in candidates
            if item.source_text == "sát nhập"
        )


class Classifiers:
    def classifier(self):
        return Classifier()


class RecordingDocuments(Documents):
    def __init__(self, written_limit: int | None = None) -> None:
        self.written_limit = written_limit
        self.written = []

    def write_annotations(
        self, source: Path, target: Path, findings, cancellation=None
    ) -> AnnotationResult:
        del source, target, cancellation
        items = list(findings)
        self.written = items if self.written_limit is None else items[: self.written_limit]
        return AnnotationResult(tuple(item.id for item in self.written))


class EmptyClassifier(Classifier):
    def classify(self, candidates, custom_prompt, cancellation):
        del candidates, custom_prompt
        cancellation.raise_if_cancelled()
        return ()


class EmptyClassifiers:
    def classifier(self):
        return EmptyClassifier()


class LegacyDictionary:
    def ignored_words(self) -> frozenset[str]:
        return frozenset({"s\u00e1t nh\u1eadp"})


def test_result_counts_only_annotations_actually_written(tmp_path: Path) -> None:
    progress: list[tuple[str, int, str]] = []
    processor = ProcessDocument(Documents(), Dictionary(), RuleEngine())
    result = processor.execute(
        ProcessRequest(tmp_path / "source.docx", tmp_path / "output.docx", Preset.STANDARD),
        lambda *event: progress.append(event),
        Token(),
    )
    assert result.finding_count == 1
    assert result.counts == {"category": {"spelling": 1}, "origin": {"rule": 1}}
    assert [percent for _, percent, _ in progress] == sorted(
        percent for _, percent, _ in progress
    )


def test_hybrid_pipeline_filters_candidates_and_records_model_provenance(tmp_path: Path) -> None:
    progress: list[tuple[str, int, str]] = []
    processor = ProcessDocument(Documents(), Dictionary(), RuleEngine(), Classifiers())
    result = processor.execute(
        ProcessRequest(
            tmp_path / "source.docx",
            tmp_path / "output.docx",
            Preset.STANDARD,
            RuleConfig(True, True, True, True, False),
            True,
            "Chỉ giữ lỗi chắc chắn",
        ),
        lambda *event: progress.append(event),
        Token(),
    )
    assert result.finding_count == 1
    assert result.counts == {"category": {"spelling": 1}, "origin": {"llm": 1}}
    assert ("model", 60, "job.classifying_candidates") in progress


def test_empty_model_response_conservatively_preserves_rule_findings(tmp_path: Path) -> None:
    documents = RecordingDocuments()
    processor = ProcessDocument(documents, Dictionary(), RuleEngine(), EmptyClassifiers())

    result = processor.execute(
        ProcessRequest(
            tmp_path / "source.docx",
            tmp_path / "output.docx",
            Preset.STANDARD,
            use_model=True,
        ),
        lambda *_: None,
        Token(),
    )

    assert result.finding_count == 2
    assert len(documents.written) == 2
    assert result.counts["origin"] == {"rule": 2}


def test_non_exportable_findings_fail_instead_of_reporting_clean(tmp_path: Path) -> None:
    processor = ProcessDocument(
        RecordingDocuments(written_limit=0), Dictionary(), RuleEngine()
    )

    with pytest.raises(ValueError, match="DOCUMENT_FINDINGS_NOT_EXPORTABLE"):
        processor.execute(
            ProcessRequest(
                tmp_path / "source.docx",
                tmp_path / "output.docx",
                Preset.STANDARD,
            ),
            lambda *_: None,
            Token(),
        )


def test_custom_prompt_requires_ready_model(tmp_path: Path) -> None:
    processor = ProcessDocument(Documents(), Dictionary(), RuleEngine())
    try:
        processor.execute(
            ProcessRequest(
                tmp_path / "source.docx",
                tmp_path / "output.docx",
                Preset.STANDARD,
                custom_prompt="Quy tắc riêng",
            ),
            lambda *_: None,
            Token(),
        )
    except ValueError as error:
        assert str(error) == "CUSTOM_PROMPT_REQUIRES_MODEL"
    else:
        raise AssertionError("custom prompt unexpectedly ran without a model")


def test_custom_prompt_uses_the_custom_rule_store_limit(tmp_path: Path) -> None:
    processor = ProcessDocument(Documents(), Dictionary(), RuleEngine())

    with pytest.raises(ValueError, match="CUSTOM_PROMPT_TOO_LONG"):
        processor.execute(
            ProcessRequest(
                tmp_path / "source.docx",
                tmp_path / "output.docx",
                Preset.STANDARD,
                use_model=True,
                custom_prompt="x" * 4_201,
            ),
            lambda *_: None,
            Token(),
        )


def test_session_ignore_words_are_combined_without_persistence(tmp_path: Path) -> None:
    processor = ProcessDocument(Documents(), Dictionary(), RuleEngine())
    result = processor.execute(
        ProcessRequest(
            tmp_path / "source.docx",
            tmp_path / "output.docx",
            Preset.STANDARD,
            ignored_words=frozenset({"sát nhập"}),
        ),
        lambda *_: None,
        Token(),
    )
    assert result.finding_count == 1
    assert result.counts == {"category": {"technical": 1}, "origin": {"rule": 1}}


def test_legacy_persistent_dictionary_no_longer_suppresses_findings(tmp_path: Path) -> None:
    documents = RecordingDocuments()
    processor = ProcessDocument(documents, LegacyDictionary(), RuleEngine())

    result = processor.execute(
        ProcessRequest(
            tmp_path / "source.docx",
            tmp_path / "output.docx",
            Preset.STANDARD,
        ),
        lambda *_: None,
        Token(),
    )

    assert any(item.source_text == "s\u00e1t nh\u1eadp" for item in documents.written)
    assert result.finding_count == 2
