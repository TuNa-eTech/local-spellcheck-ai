from soatvan.checking import Block, Preset, RuleConfig, RuleEngine


def test_presets_map_to_structured_rule_configuration() -> None:
    assert RuleConfig.for_preset(Preset.STANDARD) == RuleConfig(True, True, True, True, False, True)
    assert RuleConfig.for_preset(Preset.ADMINISTRATIVE) == RuleConfig(
        True, True, True, True, True, True
    )
    assert RuleConfig.for_preset(Preset.SPELLING) == RuleConfig(
        False, False, True, True, False, True
    )


def test_rules_are_deterministic_and_resolve_overlaps() -> None:
    findings = RuleEngine().check(
        [Block("document:p0", "Ủy ban  nhân dân sát nhập,và và và xử lí.")],
        Preset.ADMINISTRATIVE,
    )
    assert [item.detector_id for item in findings] == [
        "spacing.multiple.v1",
        "confusion.sát_nhập.v1",
        "punctuation.missing_space.v2",
        "word.repeated.v2",
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


def test_dictionary_word_suppresses_every_word_based_detector() -> None:
    findings = RuleEngine().check(
        [Block("document:p0", "ủy ban nhân dân xử lí")],
        Preset.ADMINISTRATIVE,
        frozenset({"ủy", "xử"}),
    )
    assert findings == []


def test_nfc_offsets_map_back_to_decomposed_source() -> None:
    source = "xu\u031b\u0309 lí"
    finding = RuleEngine().check([Block("document:p0", source)], Preset.STANDARD)[0]
    assert finding.suggestion == "xử lý"
    assert finding.source_text == source
    assert (finding.start, finding.end) == (0, len(source))


def test_spelling_preset_excludes_technical_rules() -> None:
    findings = RuleEngine().check(
        [Block("document:p0", "Nội  dung ngiên cứu,và")],
        Preset.SPELLING,
    )
    assert [(item.detector_id, item.suggestion) for item in findings] == [
        ("syllable.onset.v2", "nghiên")
    ]


def test_syllable_onset_repairs_are_conservative_and_dictionary_aware() -> None:
    findings = RuleEngine().check(
        [Block("document:p0", "Tài liệu gế cẻ qản ngiên API OpenAI")],
        Preset.SPELLING,
        frozenset({"cẻ"}),
    )
    assert [(item.source_text, item.suggestion) for item in findings] == [
        ("gế", "ghế"),
        ("qản", "quản"),
        ("ngiên", "nghiên"),
    ]


def test_toned_u_after_q_is_not_duplicated() -> None:
    findings = RuleEngine().check(
        [Block("document:p0", "Kết qủa kiểm tra")], Preset.SPELLING
    )

    assert [(item.source_text, item.suggestion) for item in findings] == [
        ("qủa", "quả")
    ]
    assert all(item.suggestion != "quủa" for item in findings)


def test_technical_rules_skip_urls_abbreviations_quotes_and_line_breaks() -> None:
    text = (
        "TP.HCM https://example.com lienhe@example.com Kết thúc.” ...Tiếp\n"
        ", từ\ntừ;nhưng"
    )
    findings = RuleEngine().check([Block("document:p0", text)], Preset.STANDARD)

    assert [(item.source_text, item.suggestion) for item in findings] == [
        (";", "; ")
    ]


def test_rule_limit_is_global_and_deterministic() -> None:
    blocks = [Block(f"document:p{index}", "sát nhập  ") for index in range(10)]
    first = RuleEngine().check(blocks, Preset.STANDARD, limit=5)
    second = RuleEngine().check(blocks, Preset.STANDARD, limit=5)
    assert first == second
    assert len(first) == 5


def test_explicit_rule_configuration_overrides_preset() -> None:
    config = RuleConfig(False, False, True, False, False, False)
    findings = RuleEngine().check(
        [Block("document:p0", "Nội  dung sát nhập gế")],
        Preset.STANDARD,
        config=config,
    )
    assert [item.detector_id for item in findings] == ["confusion.sát_nhập.v1"]


def test_administrative_capitalization_reason_cites_the_required_authority() -> None:
    finding = RuleEngine().check(
        [Block("document:p0", "ủy ban nhân dân ban hành")],
        Preset.ADMINISTRATIVE,
    )[0]
    assert "Nghị định 30/2020/NĐ-CP" in finding.reason
    assert "Phụ lục II" in finding.reason
