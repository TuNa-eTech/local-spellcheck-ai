from __future__ import annotations

import re
import sys
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from soatvan.checking.domain import Block, Finding, Preset, RuleConfig
from soatvan.checking.localization import canonicalize_llm_edit, localize_llm_edits
from soatvan.checking.rules import RuleEngine

from .ports import (
    CancellationToken,
    ClassificationCandidate,
    ClassifierProvider,
    ContextClassifier,
    DictionaryRepository,
    DocumentPackage,
    FullTextReviewer,
    ProgressSink,
    ReviewCandidate,
    Seq2SeqProvider,
)

# Stored rule text is capped at 4,000 characters. The transport also carries
# up to 99 blank-line separators when those records are compiled for the model.
MAX_CUSTOM_PROMPT_LENGTH = 4_200
LLM_DISCOVERY_VERSION = "v2"


@dataclass(frozen=True, slots=True)
class ProcessRequest:
    source: Path
    temporary_output: Path
    preset: Preset
    rule_config: RuleConfig | None = None
    use_model: bool = False
    use_seq2seq: bool = False
    custom_prompt: str = ""
    ignored_words: frozenset[str] = frozenset()
    full_review: bool = False
    include_rule_findings: bool = False


@dataclass(frozen=True, slots=True)
class ProcessResult:
    finding_count: int
    output_path: Path | None
    counts: dict[str, dict[str, int]]
    review: dict[str, int | str] | None = None


class ProcessDocument:
    def __init__(
        self,
        documents: DocumentPackage,
        dictionary: DictionaryRepository,
        rules: RuleEngine,
        classifiers: ClassifierProvider | None = None,
        seq2seq: Seq2SeqProvider | None = None,
    ) -> None:
        self._documents = documents
        self._dictionary = dictionary
        self._rules = rules
        self._classifiers = classifiers
        self._seq2seq = seq2seq

    def execute(
        self, request: ProcessRequest, progress: ProgressSink, cancel: CancellationToken
    ) -> ProcessResult:
        progress("reading", 10, "job.reading")
        blocks = self._documents.read_blocks(request.source)
        cancel.raise_if_cancelled()
        if request.include_rule_findings and (
            not request.use_model or not request.full_review
        ):
            raise ValueError("INCLUDE_RULE_FINDINGS_REQUIRES_FULL_REVIEW")
        if request.custom_prompt and not request.use_model:
            raise ValueError("CUSTOM_PROMPT_REQUIRES_MODEL")
        if request.full_review and not request.use_model:
            raise ValueError("FULL_REVIEW_REQUIRES_MODEL")
        if len(request.custom_prompt) > MAX_CUSTOM_PROMPT_LENGTH:
            raise ValueError("CUSTOM_PROMPT_TOO_LONG")
        # Full review always scans the complete document. Deterministic findings
        # can optionally be supplied as candidates and merged into that review;
        # the default remains the existing LLM-only behavior.
        ignored_words = request.ignored_words
        findings: list[Finding] = []
        if not request.full_review or request.include_rule_findings:
            progress("rules", 35, "job.applying_rules")
            findings = self._rules.check(
                blocks,
                request.preset,
                ignored_words,
                cancellation=cancel.raise_if_cancelled,
                config=request.rule_config,
            )
            cancel.raise_if_cancelled()

        # Seq2Seq spelling correction pass — runs independently of the LLM slot.
        # Produces additional findings from vn-spell-correction-small without
        # requiring cloud AI or a GGUF model to be installed.
        if request.use_seq2seq and self._seq2seq is not None and self._seq2seq.is_ready():
            # Sequential offload: deactivate local LLM if resident to allocate max memory for Seq2Seq
            llm_was_resident = False
            if self._classifiers is not None and hasattr(self._classifiers, "deactivate"):
                try:
                    self._classifiers.deactivate()
                    # Force aggressive memory reclamation — on macOS Metal the
                    # native allocator may hold pages until a full GC cycle runs.
                    import gc
                    gc.collect()
                    gc.collect()
                    llm_was_resident = True
                    sys.stderr.write(
                        "[SoatVan-Process] Local LLM deactivated before Seq2Seq pass.\n"
                    )
                    sys.stderr.flush()
                except Exception:
                    pass

            sys.stderr.write(
                f"[SoatVan-Process] Running Seq2Seq spell-check pass on {len(blocks)} block(s)...\n"
            )
            sys.stderr.flush()
            progress("seq2seq", 55, "job.seq2seq_correction")
            try:
                if llm_was_resident and sys.platform == "darwin":
                    # On macOS with unified memory, running seq2seq in the same
                    # process after unloading a GGUF model risks OOM-kill because
                    # Metal memory may not be fully reclaimed.  Run in a
                    # subprocess so that a crash does not take down the sidecar.
                    sys.stderr.write(
                        "[SoatVan-Process] Using subprocess isolation for Seq2Seq (macOS memory safety).\n"
                    )
                    sys.stderr.flush()
                    seq2seq_findings = _run_seq2seq_subprocess(
                        self._seq2seq, blocks, request.ignored_words, cancel
                    )
                else:
                    seq2seq_findings = self._seq2seq.check_blocks(blocks, request.ignored_words, cancel)
                sys.stderr.write(
                    f"[SoatVan-Process] Seq2Seq spell-check finished with {len(seq2seq_findings)} finding(s).\n"
                )
                sys.stderr.flush()
                findings = _merge_seq2seq_findings(findings, seq2seq_findings)
            except Exception as exc:
                if "CANCEL" in str(type(exc).__name__).upper() or "CANCEL" in str(exc).upper():
                    raise
                sys.stderr.write(
                    f"[SoatVan-Process] WARNING: Seq2Seq pass failed ({exc}). Skipping Seq2Seq and continuing.\n"
                )
                sys.stderr.flush()
            finally:
                # Sequential offload: always unload Seq2Seq to free RAM/VRAM before LLM pass
                unload_fn = getattr(self._seq2seq, "unload", None)
                if callable(unload_fn):
                    try:
                        unload_fn()
                        sys.stderr.write(
                            "[SoatVan-Process] Seq2Seq model unloaded from memory to free RAM/VRAM.\n"
                        )
                        sys.stderr.flush()
                    except Exception as unload_exc:
                        sys.stderr.write(
                            f"[SoatVan-Process] WARNING: Seq2Seq unload failed: {unload_exc}\n"
                        )
                        sys.stderr.flush()
            cancel.raise_if_cancelled()

        review_summary: dict[str, int | str] | None = None
        review_failed_block_ids: frozenset[str] = frozenset()
        if request.use_model:
            if request.full_review:
                supports_full_review = getattr(
                    self._classifiers, "supports_full_review", None
                )
                if not callable(supports_full_review) or not supports_full_review():
                    raise ValueError("MODEL_FULL_REVIEW_NOT_APPROVED")
            classifier = self._classifiers.classifier() if self._classifiers else None
            if classifier is None:
                raise ValueError("MODEL_CLASSIFIER_NOT_READY")
            if request.full_review:
                review = getattr(classifier, "review", None)
                if not callable(review):
                    raise ValueError("MODEL_FULL_REVIEW_UNSUPPORTED")
                sys.stderr.write(
                    f"[SoatVan-Process] Starting LLM Full Review on {len(blocks)} block(s)...\n"
                )
                sys.stderr.flush()
                progress("model", 40, "job.reviewing_document")

                def review_progress(processed: int, total: int) -> None:
                    percent = 40 + (30 * processed // max(1, total))
                    progress("model", percent, "job.reviewing_document")
                    sys.stderr.write(
                        f"[SoatVan-Process] LLM review chunk {processed}/{total} completed ({percent}%).\n"
                    )
                    sys.stderr.flush()

                findings, review_summary, review_failed_block_ids = _apply_full_review(
                    findings,
                    blocks,
                    cast(FullTextReviewer, classifier),
                    request.custom_prompt,
                    ignored_words,
                    cancel,
                    review_progress,
                )
            else:
                progress("model", 60, "job.classifying_candidates")
                findings = _apply_classifier(
                    findings, blocks, classifier, request.custom_prompt, cancel
                )
            cancel.raise_if_cancelled()
        if not findings:
            request.temporary_output.unlink(missing_ok=True)
            return ProcessResult(0, None, {}, review_summary)
        progress("validating", 72, "job.validating_anchors")
        cancel.raise_if_cancelled()
        progress("exporting", 88, "job.exporting")
        annotation = self._documents.write_annotations(
            request.source, request.temporary_output, findings, cancel
        )
        cancel.raise_if_cancelled()
        written_ids = frozenset(annotation.written_ids)
        unwritten = [finding for finding in findings if finding.id not in written_ids]
        if review_summary is not None and unwritten:
            failed_block_ids = review_failed_block_ids | frozenset(
                finding.block_id for finding in unwritten
            )
            review_summary = _with_failed_review_blocks(review_summary, failed_block_ids)
        elif not written_ids:
            # Findings existed, but none could be represented safely in the
            # output document. Never turn that condition into "no findings".
            raise ValueError("DOCUMENT_FINDINGS_NOT_EXPORTABLE")
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
            review_summary,
        )


def _merge_seq2seq_findings(
    rule_findings: list[Finding], seq2seq_findings: list[Finding]
) -> list[Finding]:
    """Merge seq2seq findings with rule findings, skipping overlapping spans."""
    if not seq2seq_findings:
        return rule_findings
    result = list(rule_findings)
    for s2s in seq2seq_findings:
        overlaps = any(
            s2s.block_id == r.block_id and s2s.start < r.end and r.start < s2s.end
            for r in result
        )
        if not overlaps:
            result.append(s2s)
    return result


def _run_seq2seq_subprocess(
    seq2seq: object,
    blocks: list[Block],
    ignored_words: frozenset[str],
    cancel: CancellationToken | None = None,
) -> list[Finding]:
    """Run seq2seq in an isolated subprocess to avoid OOM on macOS.

    When a GGUF model was just unloaded, macOS Metal memory may not be
    fully reclaimed.  Running torch in the same process risks Jetsam
    killing the entire sidecar.  A subprocess crash is recoverable —
    the parent logs a warning and continues without seq2seq findings.

    The subprocess is cancellable: if the user presses Stop, the parent
    kills the child process immediately.
    """
    import json
    import subprocess
    import threading

    model_dir = getattr(seq2seq, "_model_dir", None) or getattr(seq2seq, "_speller", None) and getattr(seq2seq._speller, "model_id", None)  # type: ignore[union-attr]
    if model_dir is None:
        raise RuntimeError("Cannot determine seq2seq model_dir for subprocess")

    input_data = json.dumps({
        "model_dir": str(model_dir),
        "blocks": [{"id": b.id, "text": b.text, "kind": b.kind} for b in blocks],
        "ignored_words": sorted(ignored_words),
    })

    proc = subprocess.Popen(
        [sys.executable, "-m", "soatvan.entrypoints.seq2seq_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Stream subprocess stderr to parent stderr in real-time so the user
    # sees per-block progress as it happens.
    def _stream_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            sys.stderr.buffer.write(line)
            sys.stderr.buffer.flush()

    stderr_thread = threading.Thread(target=_stream_stderr, daemon=True)
    stderr_thread.start()

    # Write stdin and read stdout in a background thread.
    stdout_data: list[bytes] = [b""]

    def _io() -> None:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(input_data.encode("utf-8"))
        proc.stdin.close()
        stdout_data[0] = proc.stdout.read()

    io_thread = threading.Thread(target=_io, daemon=True)
    io_thread.start()

    # Main thread polls for cancellation every 0.5 s.
    try:
        while io_thread.is_alive():
            if cancel is not None:
                cancel.raise_if_cancelled()
            io_thread.join(timeout=0.5)
    except Exception:
        proc.kill()
        io_thread.join(timeout=5)
        stderr_thread.join(timeout=2)
        raise

    stderr_thread.join(timeout=5)
    stdout = stdout_data[0]

    if proc.returncode != 0:
        raise RuntimeError(
            f"Seq2Seq subprocess exited with code {proc.returncode}"
        )

    result = json.loads(stdout.decode("utf-8"))
    return [
        Finding(
            id=f["id"],
            category=f["category"],
            origin=f["origin"],
            detector_id=f["detector_id"],
            block_id=f["block_id"],
            start=f["start"],
            end=f["end"],
            source_text=f["source_text"],
            suggestion=f["suggestion"],
            reason=f["reason"],
            rule_version=f["rule_version"],
            confidence=f.get("confidence", 1.0),
        )
        for f in result["findings"]
    ]


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
        if verdict is None or verdict.confidence < classifier.minimum_confidence:
            # Missing/malformed output and low-confidence decisions are not
            # evidence that a deterministic rule finding is a false positive.
            accepted.append(finding)
        elif verdict.verdict == "keep":
            accepted.append(
                replace(
                    finding,
                    origin="llm",
                    confidence=verdict.confidence,
                    rule_version=f"{finding.rule_version}+{classifier.version}",
                )
            )
        elif verdict.verdict != "drop":
            accepted.append(finding)
    return accepted


def _apply_full_review(
    findings: list[Finding],
    blocks: list[Block],
    reviewer: FullTextReviewer,
    custom_prompt: str,
    ignored_words: frozenset[str],
    cancel: CancellationToken,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[list[Finding], dict[str, int | str], frozenset[str]]:
    review_candidates = tuple(
        ReviewCandidate(
            item.id,
            item.block_id,
            item.start,
            item.end,
            item.source_text,
            item.suggestion,
            item.detector_id,
        )
        for item in findings
    )
    result = reviewer.review(
        tuple(blocks), review_candidates, custom_prompt, cancel, progress
    )
    verdicts = {item.candidate_id: item for item in result.verdicts}
    failed_blocks = set(result.failed_block_ids)
    accepted: list[Finding] = []
    for finding in findings:
        verdict = verdicts.get(finding.id)
        # A deterministic rule finding is never removed by the model here. The
        # self-reported confidence is not calibrated — small local models answer
        # with the same value for every response — so a `drop` verdict is not
        # evidence of a false positive, and silently losing a real finding is
        # the worse failure. The dedicated AI-filter mode still does filter.
        if (
            finding.block_id in failed_blocks
            or verdict is None
            or verdict.confidence < reviewer.minimum_confidence
            or verdict.verdict != "keep"
        ):
            accepted.append(finding)
        else:
            accepted.append(
                replace(
                    finding,
                    confidence=verdict.confidence,
                    rule_version=f"{finding.rule_version}+{reviewer.version}",
                )
            )

    block_text = {block.id: block.text for block in blocks}
    for proposal in result.discoveries:
        text = block_text.get(proposal.block_id)
        # The discovery-side confidence gate is deliberately absent: the value is
        # a constant for local models, so filtering on it dropped real findings
        # without buying precision. Anchor equality and the localisation quality
        # gate below are the checks that actually carry weight.
        if (
            text is None
            or proposal.start < 0
            or proposal.end > len(text)
            or proposal.start >= proposal.end
            or text[proposal.start : proposal.end] != proposal.source_text
        ):
            continue
        for relative_start, source_text, suggestion in localize_llm_edits(
            proposal.source_text, proposal.suggestion, proposal.reason_code
        ):
            start = proposal.start + relative_start
            end = start + len(source_text)
            if text[start:end] != source_text or _contains_ignored_text(
                source_text, ignored_words
            ):
                continue
            category, reason_code = canonicalize_llm_edit(
                source_text, suggestion, proposal.category, proposal.reason_code
            )
            detector = f"llm.discovery.{reason_code}.{LLM_DISCOVERY_VERSION}"
            accepted.append(
                Finding(
                    id=f"{proposal.block_id}:{start}:{end}:{detector}",
                    category=category,
                    origin="llm",
                    detector_id=detector,
                    block_id=proposal.block_id,
                    start=start,
                    end=end,
                    source_text=source_text,
                    suggestion=suggestion,
                    reason=_review_reason(reason_code),
                    rule_version=reviewer.version,
                    confidence=proposal.confidence,
                )
            )

    merged = _merge_review_findings(accepted, blocks)
    limited = _limit_review_findings(merged, blocks, 1_000)
    exported_ids = {finding.id for finding in limited}
    failed_blocks.update(
        finding.block_id for finding in merged if finding.id not in exported_ids
    )
    failed_block_ids = frozenset(failed_blocks)
    failed_block_count = len(failed_block_ids)
    summary: dict[str, int | str] = {
        "status": "partial" if result.status == "partial" or failed_blocks else "complete",
        "total_chunks": result.total_chunks,
        "reviewed_chunks": result.reviewed_chunks,
        "failed_chunks": len(result.failed_chunk_ids),
        "total_blocks": len(blocks),
        "reviewed_blocks": max(0, len(blocks) - failed_block_count),
        "failed_blocks": failed_block_count,
        "timeout_chunks": result.timeout_chunks,
        "invalid_output_chunks": result.invalid_output_chunks,
        "inference_error_chunks": result.inference_error_chunks,
        "retried_chunks": result.retried_chunks,
        "recovered_chunks": result.recovered_chunks,
    }
    return limited, summary, failed_block_ids


def _with_failed_review_blocks(
    summary: dict[str, int | str], failed_block_ids: frozenset[str]
) -> dict[str, int | str]:
    total_blocks = int(summary["total_blocks"])
    failed_blocks = min(total_blocks, len(failed_block_ids))
    return {
        **summary,
        "status": "partial",
        "reviewed_blocks": total_blocks - failed_blocks,
        "failed_blocks": failed_blocks,
    }


def _merge_review_findings(findings: list[Finding], blocks: list[Block]) -> list[Finding]:
    block_order = {block.id: index for index, block in enumerate(blocks)}
    deduplicated: dict[tuple[str, int, int, str], Finding] = {}
    for finding in findings:
        key = (finding.block_id, finding.start, finding.end, finding.suggestion)
        current = deduplicated.get(key)
        if current is None or _exact_duplicate_rank(finding) < _exact_duplicate_rank(
            current
        ):
            deduplicated[key] = finding
    winners: list[Finding] = []
    for finding in sorted(deduplicated.values(), key=_overlap_rank):
        if not any(_findings_overlap(finding, item) for item in winners):
            winners.append(finding)
    return sorted(
        winners,
        key=lambda item: (block_order.get(item.block_id, 1_000_000), item.start, item.end),
    )


def _findings_overlap(left: Finding, right: Finding) -> bool:
    return (
        left.block_id == right.block_id
        and left.start < right.end
        and right.start < left.end
    )


def _limit_review_findings(
    findings: list[Finding], blocks: list[Block], limit: int
) -> list[Finding]:
    if len(findings) <= limit:
        return findings
    selected = sorted(findings, key=_quota_rank)[:limit]
    block_order = {block.id: index for index, block in enumerate(blocks)}
    return sorted(
        selected,
        key=lambda item: (block_order.get(item.block_id, 1_000_000), item.start, item.end),
    )


def _category_priority(finding: Finding) -> int:
    category_priority = {
        "spelling": 6,
        "compound_word": 5,
        "grammar": 4,
        "word_choice": 3,
        "capitalization": 2,
        "technical": 1,
        "custom_rule": 0,
    }
    return category_priority.get(finding.category, 0)


def _is_discovery(finding: Finding) -> bool:
    return finding.detector_id.startswith("llm.discovery.")


def _exact_duplicate_rank(finding: Finding) -> tuple[int, float, str]:
    # When both sources propose the exact same edit, retain the deterministic
    # detector as provenance instead of manufacturing an AI-only duplicate.
    return (int(_is_discovery(finding)), -finding.confidence, finding.id)


def _overlap_rank(finding: Finding) -> tuple[int, int, int, float, str]:
    span_length = finding.end - finding.start
    return (
        span_length,
        0 if _is_discovery(finding) else 1,
        -_category_priority(finding),
        -finding.confidence,
        finding.id,
    )


def _quota_rank(finding: Finding) -> tuple[int, int, int, int, float, str]:
    return (
        0 if _is_discovery(finding) else 1,
        finding.end - finding.start,
        -_category_priority(finding),
        finding.start,
        -finding.confidence,
        finding.id,
    )


def _contains_ignored_text(value: str, ignored_words: frozenset[str]) -> bool:
    ignored = {
        unicodedata.normalize("NFC", word).casefold() for word in ignored_words
    }
    normalized = unicodedata.normalize("NFC", value).casefold()
    return normalized in ignored or any(
        match.group(0).casefold() in ignored
        for match in re.finditer(r"[^\W_]+", normalized, flags=re.UNICODE)
    )


def _review_reason(reason_code: str) -> str:
    return {
        "spelling": "Từ hoặc cụm từ có thể sai chính tả.",
        "diacritic": "Dấu tiếng Việt có thể được đặt chưa đúng.",
        "compound_word": "Cách viết từ ghép có thể chưa đúng.",
        "capitalization": "Cách viết hoa có thể chưa phù hợp.",
        "punctuation": "Dấu câu có thể chưa đúng vị trí hoặc cách dùng.",
        "spacing": "Khoảng trắng có thể chưa đúng.",
        "repetition": "Từ hoặc cụm từ có thể bị lặp không cần thiết.",
        "technical": "Cách trình bày kỹ thuật có thể chưa phù hợp.",
        "grammar": "Cấu trúc câu có thể chưa đúng ngữ pháp.",
        "word_choice": "Từ được dùng có thể chưa phù hợp với ngữ cảnh.",
        "custom_rule": "Nội dung có thể chưa phù hợp với quy tắc riêng.",
    }.get(reason_code, "Nội dung cần được kiểm tra lại.")
