from __future__ import annotations

from pathlib import Path

from soatvan.checking import Block, Preset, RuleEngine
from soatvan.workflow import ProcessDocument, ProcessRequest
from soatvan.workflow.ports import AnnotationResult


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
