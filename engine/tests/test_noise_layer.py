"""Layer-level tests for Vietnamese text noise generator and mutations."""
from __future__ import annotations

from soatvan.testing.noise import (
    NoiseConfig,
    NoiseGenerator,
    heavy_noise,
    light_noise,
)


class TestNoiseGeneratorLayer:
    """Test suite verifying synthetic noise generation and stability."""

    def test_noise_generator_is_deterministic_with_same_seed(self) -> None:
        text = "Ủy ban nhân dân tỉnh ban hành quyết định về việc thực hiện kế hoạch kiểm tra cán bộ."
        gen1 = NoiseGenerator(light_noise(), seed=999)
        gen2 = NoiseGenerator(light_noise(), seed=999)

        assert gen1.noisify(text) == gen2.noisify(text)

    def test_noise_generator_produces_different_output_with_different_seeds(self) -> None:
        text = "Ủy ban nhân dân tỉnh ban hành quyết định về việc thực hiện kế hoạch kiểm tra cán bộ."
        gen1 = NoiseGenerator(heavy_noise(), seed=10)
        gen2 = NoiseGenerator(heavy_noise(), seed=20)

        assert gen1.noisify(text) != gen2.noisify(text)

    def test_noise_confusion_cluster_swap(self) -> None:
        cfg = NoiseConfig(p_confusion=1.0)
        gen = NoiseGenerator(cfg, seed=42)
        # "thực hiện kỷ luật và nộp thuế đầy đủ"
        text = "thuế"
        mutated = gen.noisify(text)
        assert mutated in {"thuê", "thuệ"}

    def test_noise_diacritic_stripping(self) -> None:
        cfg = NoiseConfig(p_diacritic_strip=1.0)
        gen = NoiseGenerator(cfg, seed=42)
        text = "hợp đồng"
        mutated = gen.noisify(text)
        assert mutated == "hop dong"

    def test_noise_segment_glued_words(self) -> None:
        cfg = NoiseConfig(p_segment=1.0)
        gen = NoiseGenerator(cfg, seed=42)
        text = "bổ sung kế hoạch"
        mutated = gen.noisify(text)
        # Space should be removed, creating glued words
        assert " " not in mutated or len(mutated.split()) < 3

    def test_noise_edge_cases_safety(self) -> None:
        gen = NoiseGenerator(heavy_noise(), seed=42)
        assert gen.noisify("") == ""
        assert gen.noisify("   ") == "   "
        assert gen.noisify("12345 67890") == "12345 67890"
        assert gen.noisify("A") == "A"
        assert gen.noisify(".,:;!?") == ".,:;!?"
