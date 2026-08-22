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
