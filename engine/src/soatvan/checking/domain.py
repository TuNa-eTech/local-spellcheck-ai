from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class Preset(StrEnum):
    STANDARD = "standard"
    ADMINISTRATIVE = "administrative"
    SPELLING = "spelling"


@dataclass(frozen=True, slots=True)
class RuleConfig:
    technical: bool
    repeated_words: bool
    confusions: bool
    syllables: bool
    administrative_capitalization: bool

    @classmethod
    def for_preset(cls, preset: Preset) -> RuleConfig:
        if preset is Preset.SPELLING:
            return cls(False, False, True, True, False)
        return cls(True, True, True, True, preset is Preset.ADMINISTRATIVE)


@dataclass(frozen=True, slots=True)
class Block:
    id: str
    text: str
    kind: str = "paragraph"


@dataclass(frozen=True, slots=True)
class Finding:
    id: str
    category: str
    origin: str
    detector_id: str
    block_id: str
    start: int
    end: int
    source_text: str
    suggestion: str
    reason: str
    rule_version: str
    confidence: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
