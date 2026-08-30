"""Testing and synthetic evaluation utilities for SoatVan."""
from __future__ import annotations

from soatvan.testing.noise import NoiseConfig, NoiseGenerator, heavy_noise, light_noise

__all__ = [
    "BenchmarkReport",
    "NoiseConfig",
    "NoiseGenerator",
    "SyntheticBenchmarkRunner",
    "heavy_noise",
    "light_noise",
]


def __getattr__(name: str):
    if name in ("BenchmarkReport", "SyntheticBenchmarkRunner"):
        from soatvan.testing.benchmark import BenchmarkReport, SyntheticBenchmarkRunner

        if name == "BenchmarkReport":
            return BenchmarkReport
        return SyntheticBenchmarkRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

