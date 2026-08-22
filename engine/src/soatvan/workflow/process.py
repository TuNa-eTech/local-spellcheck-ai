from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from soatvan.checking.domain import Preset
from soatvan.checking.rules import RuleEngine

from .ports import CancellationToken, DictionaryRepository, DocumentPackage, ProgressSink


@dataclass(frozen=True, slots=True)
class ProcessRequest:
    source: Path
    temporary_output: Path
    preset: Preset


@dataclass(frozen=True, slots=True)
class ProcessResult:
    finding_count: int
    output_path: Path | None
    counts: dict[str, dict[str, int]]


class ProcessDocument:
    def __init__(
        self, documents: DocumentPackage, dictionary: DictionaryRepository, rules: RuleEngine
    ) -> None:
        self._documents = documents
        self._dictionary = dictionary
        self._rules = rules

    def execute(
        self, request: ProcessRequest, progress: ProgressSink, cancel: CancellationToken
    ) -> ProcessResult:
        progress("reading", 10, "job.reading")
        blocks = self._documents.read_blocks(request.source)
        cancel.raise_if_cancelled()
        progress("rules", 35, "job.applying_rules")
        findings = self._rules.check(
            blocks,
            request.preset,
            self._dictionary.ignored_words(),
            cancellation=cancel.raise_if_cancelled,
        )
        cancel.raise_if_cancelled()
        if not findings:
            request.temporary_output.unlink(missing_ok=True)
            return ProcessResult(0, None, {})
        progress("validating", 72, "job.validating_anchors")
        cancel.raise_if_cancelled()
        progress("exporting", 88, "job.exporting")
        annotation = self._documents.write_annotations(
            request.source, request.temporary_output, findings, cancel
        )
        cancel.raise_if_cancelled()
        written_ids = frozenset(annotation.written_ids)
        category_counts: dict[str, int] = {}
        origin_counts: dict[str, int] = {}
        for finding in findings:
            if finding.id in written_ids:
                category_counts[finding.category] = category_counts.get(finding.category, 0) + 1
                origin_counts[finding.origin] = origin_counts.get(finding.origin, 0) + 1
        progress("complete", 100, "job.complete")
        return ProcessResult(
            annotation.count,
            request.temporary_output if annotation.count else None,
            {"category": category_counts, "origin": origin_counts},
        )
