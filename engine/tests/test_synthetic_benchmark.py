"""Tests for the synthetic accuracy benchmark runner."""
from __future__ import annotations

from soatvan.testing.benchmark import SyntheticBenchmarkRunner
from soatvan.testing.noise import light_noise


def test_synthetic_benchmark_runner_executes_and_generates_report() -> None:
    corpus = [
        "Thực hiện theo đúng quy định của pháp luật hiện hành và văn bản hướng dẫn.",
        "Cần kiểm tra kỹ lưỡng các số liệu trong báo cáo trước khi trình lãnh đạo ký.",
    ]
    runner = SyntheticBenchmarkRunner(corpus)
    report = runner.run(light_noise(), seed=42, level_name="light")

    assert report.total_sentences == 2
    assert report.total_tokens > 0
    assert 0.0 <= report.precision <= 1.0
    assert 0.0 <= report.recall <= 1.0
    assert 0.0 <= report.f1_score <= 1.0
    assert report.throughput_tok_per_sec > 0.0

    summary_text = report.summary()
    assert "SOATVAN SYNTHETIC ACCURACY BENCHMARK" in summary_text
    assert "Precision:" in summary_text
    assert "Recall:" in summary_text
    assert "F1-Score:" in summary_text
