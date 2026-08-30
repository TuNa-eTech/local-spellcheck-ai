"""Tests for Vietnamese text noise generator."""
from __future__ import annotations

from soatvan.testing.noise import NoiseConfig, NoiseGenerator, heavy_noise, light_noise


def test_noise_generator_is_deterministic() -> None:
    text = "Hợp đồng này được lập tại Hà Nội ngày 15 tháng 8 năm 2026."
    gen1 = NoiseGenerator(light_noise(), seed=123)
    gen2 = NoiseGenerator(light_noise(), seed=123)

    out1 = gen1.noisify(text)
    out2 = gen2.noisify(text)

    assert out1 == out2
    assert isinstance(out1, str)


def test_heavy_noise_mutates_tokens() -> None:
    text = "Thực hiện kiểm tra công tác văn thư và xử lý nghiêm các trường hợp vi phạm quy định."
    gen = NoiseGenerator(heavy_noise(), seed=42)
    noisy = gen.noisify(text)

    assert noisy != text
    assert len(noisy) > 0


def test_noise_generator_handles_edge_cases() -> None:
    gen = NoiseGenerator(light_noise(), seed=42)
    assert gen.noisify("") == ""
    assert gen.noisify("A") == "A"
