from __future__ import annotations

import json
import os
import time
from pathlib import Path

from soatvan.checking import Block, Preset, RuleEngine


def test_curated_rule_corpus_meets_regression_gate() -> None:
    corpus_path = Path(__file__).parent / "quality" / "rule-corpus.json"
    cases = json.loads(corpus_path.read_text(encoding="utf-8"))
    true_positive = 0
    predicted = 0
    expected_count = 0
    for case in cases:
        expected = {tuple(item) for item in case["expected"]}
        findings = RuleEngine().check(
            [Block(f"document:{case['id']}", case["text"])],
            Preset(case["preset"]),
            frozenset(case.get("ignored", [])),
        )
        actual = {(finding.detector_id, finding.source_text) for finding in findings}
        true_positive += len(actual & expected)
        predicted += len(actual)
        expected_count += len(expected)
    precision = true_positive / predicted
    recall = true_positive / expected_count
    assert precision >= 0.90
    assert recall >= 0.85


def test_rule_layer_processes_normalized_fifty_page_corpus_under_two_seconds() -> None:
    # A normalized page is fixed at 500 whitespace-separated syllables so the
    # benchmark is repeatable across Windows runners and does not depend on Word layout.
    page = " ".join(["nghiên", "cứu", "văn", "bản", "hành", "chính"] * 84)[:4000]
    blocks = [Block(f"document:p{index}", page) for index in range(50)]
    started = time.perf_counter()
    findings = RuleEngine().check(blocks, Preset.STANDARD)
    elapsed = time.perf_counter() - started
    assert findings == []
    max_duration = 4.0 if os.environ.get("CI") else 2.5
    assert elapsed <= max_duration, f"rule layer took {elapsed:.3f}s (budget: {max_duration}s)"
