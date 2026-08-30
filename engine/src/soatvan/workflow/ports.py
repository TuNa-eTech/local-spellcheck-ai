from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from soatvan.checking.domain import Block, Finding

ProgressSink = Callable[[str, int, str], None]


@dataclass(frozen=True, slots=True)
class AnnotationResult:
    written_ids: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.written_ids)


class CancellationToken(Protocol):
    def raise_if_cancelled(self) -> None: ...


class DocumentPackage(Protocol):
    def inspect(self, source: Path) -> dict[str, object]: ...
    def read_blocks(self, source: Path) -> list[Block]: ...
    def write_annotations(
        self,
        source: Path,
        target: Path,
        findings: Iterable[Finding],
        cancellation: CancellationToken | None = None,
    ) -> AnnotationResult: ...


class DictionaryRepository(Protocol):
    def ignored_words(self) -> frozenset[str]: ...


@dataclass(frozen=True, slots=True)
class ClassificationCandidate:
    candidate_id: str
    paragraph_id: str
    source_text: str
    suggestion: str
    reason_code: str
    occurrence_index: int
    context: str


@dataclass(frozen=True, slots=True)
class ClassifierVerdict:
    candidate_id: str
    verdict: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ReviewCandidate:
    candidate_id: str
    block_id: str
    start: int
    end: int
    source_text: str
    suggestion: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class DiscoveryProposal:
    block_id: str
    start: int
    end: int
    source_text: str
    suggestion: str
    category: str
    reason_code: str
    confidence: float


@dataclass(frozen=True, slots=True)
class FullReviewResult:
    verdicts: tuple[ClassifierVerdict, ...]
    discoveries: tuple[DiscoveryProposal, ...]
    total_chunks: int
    reviewed_chunks: int
    failed_chunk_ids: tuple[str, ...] = ()
    failed_block_ids: tuple[str, ...] = ()
    timeout_chunks: int = 0
    invalid_output_chunks: int = 0
    inference_error_chunks: int = 0
    retried_chunks: int = 0
    recovered_chunks: int = 0

    @property
    def status(self) -> str:
        return "complete" if not self.failed_chunk_ids else "partial"


class ContextClassifier(Protocol):
    @property
    def version(self) -> str: ...

    @property
    def minimum_confidence(self) -> float: ...

    def classify(
        self,
        candidates: tuple[ClassificationCandidate, ...],
        custom_prompt: str,
        cancellation: CancellationToken,
    ) -> tuple[ClassifierVerdict, ...]: ...


class FullTextReviewer(Protocol):
    @property
    def version(self) -> str: ...

    @property
    def minimum_confidence(self) -> float: ...

    def review(
        self,
        blocks: tuple[Block, ...],
        candidates: tuple[ReviewCandidate, ...],
        custom_prompt: str,
        cancellation: CancellationToken,
        progress: Callable[[int, int], None] | None = None,
    ) -> FullReviewResult: ...


class ClassifierProvider(Protocol):
    def classifier(self) -> ContextClassifier | None: ...

    def supports_full_review(self) -> bool: ...


class Seq2SeqProvider(Protocol):
    """Provider for a lightweight seq2seq spelling correction model (e.g. nrl-ai/vn-spell-correction-small).

    Unlike llama.cpp GGUF classifiers this model runs directly inside the Python
    engine process via Hugging Face Transformers / safetensors and produces
    additional `Finding` objects at the block level without requiring LLM
    classification approval.
    """

    def is_ready(self) -> bool: ...

    def check_blocks(
        self,
        blocks: list[Block],
        ignored_words: frozenset[str],
        cancellation: CancellationToken,
    ) -> list[Finding]: ...
