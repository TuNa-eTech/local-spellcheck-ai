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


def test_dictionary_check_recovers_a_glued_word() -> None:
    # Only the dictionary detector runs so the assertion cannot be satisfied by
    # the hardcoded CONFUSIONS whitelist.
    config = RuleConfig(False, False, False, False, False, True)
    findings = RuleEngine().check(
        [Block("document:p0", "Cần bổsung hồ sơ.")],
        Preset.STANDARD,
        config=config,
    )
    assert [(item.detector_id, item.source_text, item.suggestion) for item in findings] == [
        ("dictionary.syllable.v1", "bổsung", "bổ sung")
    ]


def test_dictionary_check_warns_without_guessing_an_unsupported_split() -> None:
    config = RuleConfig(False, False, False, False, False, True)
    findings = RuleEngine().check(
        [Block("document:p0", "Thực hiện nghiênthực.")],
        Preset.STANDARD,
        config=config,
    )
    # "nghiên thực" is absent from the compound list, so the detector must warn
    # instead of inventing an unsupported split.
    assert [(item.detector_id, item.suggestion) for item in findings] == [
        ("dictionary.unknown.v1", "")
    ]


def test_administrative_and_standardization_sentence() -> None:
    text = "Thực hiện Công văn số 128/CV-SNV ngày 03/8/2026 của sở Nội vụ về việc tăng cường kỉ cương hành chính"
    findings = RuleEngine().check(
        [Block("document:p0", text)],
        Preset.ADMINISTRATIVE,
    )
    assert [(item.source_text, item.suggestion, item.category) for item in findings] == [
        ("sở Nội vụ", "Sở Nội vụ", "capitalization"),
        ("kỉ cương", "kỷ cương", "spelling"),
    ]


def test_confusions_catches_neu_ro_and_administrative_homophones() -> None:
    text = "Báo cáo cần nêu rỏ nguyên nhân, bố chí nhân lực và đề suất giải pháp che dấu tồn tại."
    findings = RuleEngine().check(
        [Block("document:p0", text)],
        Preset.STANDARD,
    )
    sources_and_sugs = [(f.source_text, f.suggestion) for f in findings]
    assert ("nêu rỏ", "nêu rõ") in sources_and_sugs
    assert ("bố chí", "bố trí") in sources_and_sugs
    assert ("đề suất", "đề xuất") in sources_and_sugs
    assert ("che dấu", "che giấu") in sources_and_sugs


def test_dynamic_compound_confusion_detects_tone_swap_variants() -> None:
    text = "Cán bộ đùn đẫy trách nhiệm, hướng dẩn chưa rỏ ràng và thiếu biểu mẩu."
    findings = RuleEngine().check(
        [Block("document:p0", text)],
        Preset.STANDARD,
    )
    sources_and_sugs = [(f.source_text, f.suggestion) for f in findings]
    assert ("đùn đẫy", "đùn đẩy") in sources_and_sugs
    assert ("hướng dẩn", "hướng dẫn") in sources_and_sugs
    assert ("rỏ ràng", "rõ ràng") in sources_and_sugs
    assert ("biểu mẩu", "biểu mẫu") in sources_and_sugs


def test_target_words_kipthoi_neu_ro_theo_gioi() -> None:
    text = "Cần xử lý kịpthời, nêu rỏ số liệu và theo giỏi tiến độ."
    findings = RuleEngine().check(
        [Block("document:p0", text)],
        Preset.STANDARD,
    )
    results = {f.source_text: f.suggestion for f in findings}
    assert results.get("kịpthời") == "kịp thời"
    assert results.get("nêu rỏ") == "nêu rõ"
    assert results.get("theo giỏi") == "theo dõi"


def test_glued_words_and_glued_misspelled_words_detection() -> None:
    text = "Cán bộ cần bổsung hồ sơ, thựchiện kếhoạch, không được đùnđẫy, bốchí hợp lý và sắpsếp ngăn nắp."
    findings = RuleEngine().check(
        [Block("document:p0", text)],
        Preset.STANDARD,
    )
    results = {f.source_text: f.suggestion for f in findings}
    # Pure glued words
    assert results.get("bổsung") == "bổ sung"
    assert results.get("thựchiện") == "thực hiện"
    assert results.get("kếhoạch") == "kế hoạch"
    # Glued + tone typo
    assert results.get("đùnđẫy") == "đùn đẩy"
    # Glued + consonant typo
    assert results.get("bốchí") == "bố trí"
    assert results.get("sắpsếp") == "sắp xếp"


def test_glued_ascii_unikey_words_detection() -> None:
    text = "Các tập tin giayphep, donnghi, xacnhan, hopdong, baocao, kehoach, quyetdinh, thongbao cần được xử lý."
    findings = RuleEngine().check(
        [Block("document:p0", text)],
        Preset.STANDARD,
    )
    results = {f.source_text: f.suggestion for f in findings}
    assert results.get("giayphep") == "giấy phép"
    assert results.get("donnghi") == "đơn nghỉ"
    assert results.get("xacnhan") == "xác nhận"
    assert results.get("hopdong") == "hợp đồng"
    assert results.get("baocao") == "báo cáo"
    assert results.get("kehoach") == "kế hoạch"
    assert results.get("quyetdinh") == "quyết định"
    assert results.get("thongbao") == "thông báo"


def test_unaccented_administrative_phrases_detection() -> None:
    text = "Thực hiện hop dong và ban hành quyet dinh kiểm tra to chuc can bo theo quy trinh."
    findings = RuleEngine().check(
        [Block("document:p0", text)],
        Preset.STANDARD,
    )
    results = {f.source_text: f.suggestion for f in findings}
    assert results.get("hop dong") == "hợp đồng"
    assert results.get("quyet dinh") == "quyết định"
    assert results.get("to chuc") == "tổ chức"
    assert results.get("can bo") == "cán bộ"
    assert results.get("quy trinh") == "quy trình"





