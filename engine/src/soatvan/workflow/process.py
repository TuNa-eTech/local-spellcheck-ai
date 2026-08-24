from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from soatvan.checking.domain import Block, Finding, Preset, RuleConfig
from soatvan.checking.rules import RuleEngine

from .ports import (
    CancellationToken,
    ClassificationCandidate,
    ClassifierProvider,
    ContextClassifier,
    DictionaryRepository,
    DocumentPackage,
    ProgressSink,
)


@dataclass(frozen=True, slots=True)
class ProcessRequest:
    source: Path
    temporary_output: Path
    preset: Preset
    rule_config: RuleConfig | None = None
    use_model: bool = False
    custom_prompt: str = ""
    ignored_words: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ProcessResult:
    finding_count: int
    output_path: Path | None
    counts: dict[str, dict[str, int]]


class ProcessDocument:
    def __init__(
        self,
        documents: DocumentPackage,
        dictionary: DictionaryRepository,
        rules: RuleEngine,
        classifiers: ClassifierProvider | None = None,
    ) -> None:
        self._documents = documents
        self._dictionary = dictionary
        self._rules = rules
        self._classifiers = classifiers

    def execute(
        self, request: ProcessRequest, progress: ProgressSink, cancel: CancellationToken
    ) -> ProcessResult:
        progress("reading", 10, "job.reading")
        blocks = self._documents.read_blocks(request.source)
        cancel.raise_if_cancelled()
        progress("rules", 35, "job.applying_rules")
        ignored_words = self._dictionary.ignored_words() | request.ignored_words
        findings = self._rules.check(
            blocks,
            request.preset,
            ignored_words,
            cancellation=cancel.raise_if_cancelled,
            config=request.rule_config,
        )
        cancel.raise_if_cancelled()
        if request.custom_prompt and not request.use_model:
            raise ValueError("CUSTOM_PROMPT_REQUIRES_MODEL")
        if len(request.custom_prompt) > 1000:
            raise ValueError("CUSTOM_PROMPT_TOO_LONG")
        if request.use_model:
            classifier = self._classifiers.classifier() if self._classifiers else None
            if classifier is None:
                raise ValueError("MODEL_CLASSIFIER_NOT_READY")
            progress("model", 60, "job.classifying_candidates")
            findings = _apply_classifier(
                findings, blocks, classifier, request.custom_prompt, cancel
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


def _apply_classifier(
    findings: list[Finding],
    blocks: list[Block],
    classifier: ContextClassifier,
    custom_prompt: str,
    cancel: CancellationToken,
) -> list[Finding]:
    block_text = {block.id: block.text for block in blocks}
    candidates: list[ClassificationCandidate] = []
    for finding in findings:
        text = block_text.get(finding.block_id, "")
        occurrence = text.count(finding.source_text, 0, finding.start)
        context_start = max(0, finding.start - 240)
        context_end = min(len(text), finding.end + 240)
        candidates.append(
            ClassificationCandidate(
                candidate_id=finding.id,
                paragraph_id=finding.block_id,
                source_text=finding.source_text,
                suggestion=finding.suggestion,
                reason_code=finding.detector_id,
                occurrence_index=occurrence,
                context=text[context_start:context_end],
            )
        )
    verdicts = classifier.classify(tuple(candidates), custom_prompt, cancel)
    by_id = {item.candidate_id: item for item in verdicts}
    accepted: list[Finding] = []
    for finding in findings:
        verdict = by_id.get(finding.id)
        if (
            verdict is not None
            and verdict.verdict == "keep"
            and verdict.confidence >= classifier.minimum_confidence
        ):
            accepted.append(
                replace(
                    finding,
                    origin="llm",
                    confidence=verdict.confidence,
                    rule_version=f"{finding.rule_version}+{classifier.version}",
                )
            )
    return accepted
