"""Synthetic Accuracy Benchmark Runner for SoatVan.

Uses NoiseGenerator (inspired by nom-vn error taxonomy) to generate realistic
synthetic typos on clean administrative corpora, runs RuleEngine, and calculates
Precision, Recall, F1-score, and throughput.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from soatvan.checking.domain import Block, Preset
from soatvan.checking.rules import RuleEngine
from soatvan.testing.noise import NoiseConfig, NoiseGenerator, heavy_noise, light_noise

DEFAULT_BENCHMARK_CORPUS = [
    "Căn cứ Nghị định số 30/2020/NĐ-CP ngày 05 tháng 3 năm 2020 của Chính phủ về công tác văn thư.",
    "Thực hiện Công văn số 128/CV-SNV ngày 03/8/2026 của Sở Nội vụ về việc tăng cường kỷ cương hành chính.",
    "Ủy ban nhân dân tỉnh yêu cầu các sở, ban, ngành nghiêm túc triển khai thực hiện kế hoạch đã đề ra.",
    "Thủ trưởng các đơn vị chịu trách nhiệm trước Chủ tịch Ủy ban nhân dân thành phố về tiến độ công việc.",
    "Văn phòng Hội đồng nhân dân và Ủy ban nhân dân có trách nhiệm theo dõi, đôn đốc và tổng hợp báo cáo.",
    "Đề nghị các phòng chuyên môn rà soát quy trình, hoàn thiện hồ sơ và gửi về Phòng Kế hoạch - Tài chính.",
    "Các cơ quan, tổ chức có trách nhiệm quản lý, sử dụng con dấu và thiết bị lưu khóa bí mật theo quy định.",
    "Chánh Văn phòng, Giám đốc các Sở, Thủ trưởng các cơ quan thuộc Ủy ban nhân dân chịu trách nhiệm thi hành.",
    "Quyết định này có hiệu lực thi hành kể từ ngày ký và thay thế các quy định trước đây trái với Quyết định này.",
    "Biên bản cuộc họp được lập thành hai bản có giá trị pháp lý như nhau, mỗi bên giữ một bản.",
]


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    """Quantitative quality metrics resulting from a synthetic benchmark run."""

    noise_level: str
    total_sentences: int
    total_tokens: int
    mutated_tokens: int
    detected_findings: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1_score: float
    elapsed_seconds: float
    throughput_tok_per_sec: float

    def summary(self) -> str:
        return (
            f"=== SOATVAN SYNTHETIC ACCURACY BENCHMARK ===\n"
            f"Noise Level:        {self.noise_level}\n"
            f"Corpus Size:        {self.total_sentences} sentences ({self.total_tokens} tokens)\n"
            f"Mutated Typos:      {self.mutated_tokens}\n"
            f"Detected Findings:  {self.detected_findings}\n"
            f"--------------------------------------------\n"
            f"True Positives:     {self.true_positives}\n"
            f"False Positives:    {self.false_positives}\n"
            f"False Negatives:    {self.false_negatives}\n"
            f"--------------------------------------------\n"
            f"Precision:          {self.precision * 100:.2f}%\n"
            f"Recall:             {self.recall * 100:.2f}%\n"
            f"F1-Score:           {self.f1_score * 100:.2f}%\n"
            f"Throughput:         {self.throughput_tok_per_sec:,.0f} tokens/sec\n"
            f"Elapsed Time:       {self.elapsed_seconds:.3f}s\n"
            f"============================================"
        )


class SyntheticBenchmarkRunner:
    """Executes synthetic typo benchmarks against RuleEngine."""

    def __init__(self, corpus: list[str] | None = None) -> None:
        self.corpus = corpus or DEFAULT_BENCHMARK_CORPUS
        self.engine = RuleEngine()

    def run(self, config: NoiseConfig | None = None, seed: int = 42, level_name: str = "light") -> BenchmarkReport:
        cfg = config or light_noise()
        gen = NoiseGenerator(cfg, seed=seed)

        total_tokens = 0
        mutated_tokens = 0
        true_positives = 0
        false_positives = 0
        detected_findings = 0

        start_time = time.perf_counter()

        for idx, clean_sentence in enumerate(self.corpus):
            clean_words = clean_sentence.split()
            total_tokens += len(clean_words)

            noisy_sentence = gen.noisify(clean_sentence)
            noisy_words = noisy_sentence.split()

            # Identify mutated word indices
            mutated_set: set[str] = set()
            for w_clean, w_noisy in zip(clean_words, noisy_words, strict=False):
                if w_clean != w_noisy:
                    mutated_set.add(w_noisy)
            mutated_tokens += len(mutated_set)

            # Run engine
            block = Block(f"doc:p{idx}", noisy_sentence)
            findings = self.engine.check([block], Preset.STANDARD)
            detected_findings += len(findings)

            found_sources = {f.source_text for f in findings}
            for found in found_sources:
                if any(m in found or found in m for m in mutated_set):
                    true_positives += 1
                else:
                    # Could be finding in originally ambiguous word or false positive
                    false_positives += 1

        elapsed = time.perf_counter() - start_time
        false_negatives = max(0, mutated_tokens - true_positives)

        precision = (
            true_positives / (true_positives + false_positives)
            if (true_positives + false_positives) > 0
            else 1.0
        )
        recall = (
            true_positives / (true_positives + false_negatives)
            if (true_positives + false_negatives) > 0
            else 1.0
        )
        f1 = (
            (2 * precision * recall) / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )
        throughput = total_tokens / elapsed if elapsed > 0 else 0.0

        return BenchmarkReport(
            noise_level=level_name,
            total_sentences=len(self.corpus),
            total_tokens=total_tokens,
            mutated_tokens=mutated_tokens,
            detected_findings=detected_findings,
            true_positives=true_positives,
            false_positives=false_positives,
            false_negatives=false_negatives,
            precision=precision,
            recall=recall,
            f1_score=f1,
            elapsed_seconds=elapsed,
            throughput_tok_per_sec=throughput,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SoatVan synthetic accuracy benchmark.")
    parser.add_argument(
        "--level",
        choices=["light", "heavy"],
        default="light",
        help="Noise level to apply (default: light)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=10,
        help="Number of times to duplicate the default corpus (default: 10)",
    )
    args = parser.parse_args()

    corpus = DEFAULT_BENCHMARK_CORPUS * args.repeat
    runner = SyntheticBenchmarkRunner(corpus)
    cfg = light_noise() if args.level == "light" else heavy_noise()

    report = runner.run(cfg, seed=args.seed, level_name=args.level)
    print(report.summary())


if __name__ == "__main__":
    main()
