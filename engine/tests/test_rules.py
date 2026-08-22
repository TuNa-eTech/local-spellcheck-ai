from soatvan.checking import Block, Preset, RuleEngine


def test_rules_are_deterministic_and_resolve_overlaps() -> None:
    findings = RuleEngine().check(
        [Block("document:p0", "Ủy ban  nhân dân sát nhập,và và và xử lí.")],
        Preset.ADMINISTRATIVE,
    )
    assert [item.detector_id for item in findings] == [
        "spacing.multiple.v1",
        "confusion.sát_nhập.v1",
        "punctuation.missing_space.v1",
        "word.repeated.v1",
        "confusion.xử_lí.v1",
    ]
    assert all(
        next_item.start >= item.end for item, next_item in zip(findings, findings[1:], strict=False)
    )


def test_dictionary_suppresses_confusion() -> None:
    findings = RuleEngine().check(
        [Block("document:p0", "Thuật ngữ xử lí được chấp nhận.")],
        Preset.STANDARD,
        frozenset({"xử lí"}),
    )
    assert findings == []


def test_nfc_offsets_map_back_to_decomposed_source() -> None:
    source = "xu\u031b\u0309 lí"
    finding = RuleEngine().check([Block("document:p0", source)], Preset.STANDARD)[0]
    assert finding.suggestion == "xử lý"
    assert finding.source_text == source
    assert (finding.start, finding.end) == (0, len(source))
