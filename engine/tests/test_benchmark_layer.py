"""Layer-level tests for Synthetic Benchmark Runner and Metrics."""
from __future__ import annotations

from soatvan.testing.benchmark import BenchmarkReport, SyntheticBenchmarkRunner
from soatvan.testing.noise import heavy_noise, light_noise


class TestSyntheticBenchmarkLayer:
    """Test suite verifying benchmark runner calculations and outputs."""

    def test_benchmark_runner_computes_valid_metrics_on_light_noise(self) -> None:
        corpus = [
            "Căn cứ Nghị định số 30/2020/NĐ-CP về công tác văn thư.",
            "Ủy ban nhân dân tỉnh yêu cầu các sở ban ngành thực hiện nghiêm túc.",
            "Thủ trưởng các đơn vị chịu trách nhiệm trước Chủ tịch Ủy ban nhân dân.",
        ]
        runner = SyntheticBenchmarkRunner(corpus)
        report = runner.run(light_noise(), seed=42, level_name="light")

        assert isinstance(report, BenchmarkReport)
        assert report.noise_level == "light"
        assert report.total_sentences == 3
        assert report.total_tokens > 0
        assert 0.0 <= report.precision <= 1.0
        assert 0.0 <= report.recall <= 1.0
        assert 0.0 <= report.f1_score <= 1.0
        assert report.throughput_tok_per_sec > 0

    def test_benchmark_runner_on_heavy_noise(self) -> None:
        corpus = [
            "Quyết định này có hiệu lực thi hành kể từ ngày ký.",
            "Biên bản cuộc họp được lập thành hai bản có giá trị pháp lý như nhau.",
        ]
        runner = SyntheticBenchmarkRunner(corpus)
        report = runner.run(heavy_noise(), seed=100, level_name="heavy")

        assert report.noise_level == "heavy"
        assert report.mutated_tokens >= 1
        assert report.elapsed_seconds > 0

    def test_benchmark_report_summary_formatting(self) -> None:
        report = BenchmarkReport(
            noise_level="test",
            total_sentences=10,
            total_tokens=250,
            mutated_tokens=25,
            detected_findings=20,
            true_positives=18,
            false_positives=2,
            false_negatives=7,
            precision=0.90,
            recall=0.72,
            f1_score=0.80,
            elapsed_seconds=0.015,
            throughput_tok_per_sec=16666.6,
        )
        summary = report.summary()
        assert "Noise Level:        test" in summary
        assert "Precision:          90.00%" in summary
        assert "Recall:             72.00%" in summary
        assert "F1-Score:           80.00%" in summary
