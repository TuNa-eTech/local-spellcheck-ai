from __future__ import annotations

import unicodedata

from hypothesis import given, settings
from hypothesis import strategies as st

from soatvan.checking import Block, Preset, RuleEngine


@given(
    prefix=st.text(alphabet="abc 123", max_size=24),
    suffix=st.text(alphabet="xyz 789", max_size=24),
    normalization=st.sampled_from(["NFC", "NFD"]),
)
def test_nfc_source_mapping_always_points_to_original_text(
    prefix: str, suffix: str, normalization: str
) -> None:
    phrase = unicodedata.normalize(normalization, "xử lí")
    source = f"{prefix}|{phrase}|{suffix}"
    findings = RuleEngine().check([Block("document:p0", source)], Preset.SPELLING)
    finding = next(item for item in findings if item.detector_id == "confusion.xử_lí.v1")
    assert source[finding.start : finding.end] == phrase
    assert finding.source_text == phrase


@settings(max_examples=80, deadline=None)
@given(
    tokens=st.lists(
        st.sampled_from(["sát nhập", "xử lí", "hai  chỗ", "và và", "ngiên"]),
        min_size=1,
        max_size=20,
    )
)
def test_arbitration_never_returns_invalid_or_overlapping_anchors(tokens: list[str]) -> None:
    source = ". ".join(tokens)
    findings = RuleEngine().check([Block("document:p0", source)], Preset.STANDARD)
    for finding in findings:
        assert 0 <= finding.start < finding.end <= len(source)
        assert source[finding.start : finding.end] == finding.source_text
    for left, right in zip(findings, findings[1:], strict=False):
        assert left.end <= right.start
