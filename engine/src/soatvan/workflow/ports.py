from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol

from soatvan.checking.domain import Block, Finding

ProgressSink = Callable[[str, int, str], None]


class DocumentPackage(Protocol):
    def inspect(self, source: Path) -> dict[str, object]: ...
    def read_blocks(self, source: Path) -> list[Block]: ...
    def write_annotations(self, source: Path, target: Path, findings: Iterable[Finding]) -> int: ...


class DictionaryRepository(Protocol):
    def ignored_words(self) -> frozenset[str]: ...


class CancellationToken(Protocol):
    def raise_if_cancelled(self) -> None: ...
