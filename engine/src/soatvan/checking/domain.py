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
    dictionary: bool

    @classmethod
    def for_preset(cls, preset: Preset) -> RuleConfig:
        if preset is Preset.SPELLING:
            return cls(False, False, True, True, False, True)
        return cls(True, True, True, True, preset is Preset.ADMINISTRATIVE, True)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> RuleConfig:
        fields = (
            "technical",
            "repeated_words",
            "confusions",
            "syllables",
            "administrative_capitalization",
            "dictionary",
        )
        if set(value) != set(fields) or any(not isinstance(value[field], bool) for field in fields):
            raise ValueError("RULE_CONFIG_INVALID")
        return cls(**{field: value[field] for field in fields})  # type: ignore[arg-type]


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
