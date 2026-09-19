from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from soatvan import __version__ as engine_version
from soatvan.models import classifier as classifier_module
from soatvan.models.classifier import ModelLoadFailed, _default_runtime_factory
from soatvan.models.gpu import (
    OffloadGuard,
    backend_names,
    backend_report,
    build_id,
    local_data_dir,
    offload_allowed,
    offload_mode,
)

CUDA_SYSTEM_INFO = (
    "CUDA : ARCHS = 750,860,890 | USE_GRAPHS = 1 | PEER_MAX_BATCH_SIZE = 128 | "
    "CPU : SSE3 = 1 | AVX = 1 | AVX2 = 1 | LLAMAFILE = 1 | "
)


class FakeLlama:
    """Just enough of the llama_cpp module surface for the runtime factory."""

    def __init__(
        self,
        *,
        supports_offload: bool = True,
        system_info: str = CUDA_SYSTEM_INFO,
        gpu_load_error: Exception | None = None,
    ) -> None:
        self._supports_offload = supports_offload
        self._system_info = system_info
        self._gpu_load_error = gpu_load_error
        self.requested_gpu_layers: list[int] = []
        self.requested_context_sizes: list[int] = []

    def llama_supports_gpu_offload(self) -> bool:
        return self._supports_offload

    def llama_print_system_info(self) -> bytes:
        return self._system_info.encode("utf-8")

    def Llama(self, **kwargs: Any) -> Any:  # noqa: N802 - mirrors the native name
        self.requested_gpu_layers.append(int(kwargs["n_gpu_layers"]))
        self.requested_context_sizes.append(int(kwargs["n_ctx"]))
        if kwargs["n_gpu_layers"] != 0 and self._gpu_load_error is not None:
            raise self._gpu_load_error
        return object()


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SOATVAN_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SOATVAN_GPU_OFFLOAD", raising=False)
    return tmp_path


def test_backend_names_reads_one_name_per_backend_group() -> None:
    assert backend_names(CUDA_SYSTEM_INFO) == ["CUDA", "CPU"]
    assert backend_names("MTL : EMBED_LIBRARY = 1 | CPU : NEON = 1 | ARM_FMA = 1 | ") == [
        "MTL",
        "CPU",
    ]
    assert backend_names("") == []


def test_backend_report_survives_a_runtime_that_cannot_answer() -> None:
    class Hostile:
        def llama_supports_gpu_offload(self) -> bool:
            raise OSError("driver gone")

        def llama_print_system_info(self) -> bytes:
            raise OSError("driver gone")

    report = backend_report(Hostile())
    assert report == {"supports_offload": False, "backends": [], "system_info": ""}

    report = backend_report(FakeLlama())
    assert report["supports_offload"] is True
    assert report["backends"] == ["CUDA", "CPU"]


def test_local_data_dir_prefers_the_explicit_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    monkeypatch.setenv("SOATVAN_DATA_DIR", str(tmp_path / "explicit"))
    assert local_data_dir() == tmp_path / "explicit"
    monkeypatch.delenv("SOATVAN_DATA_DIR")
    assert local_data_dir() == tmp_path / "AppData" / "SoatVan"


def test_offload_mode_falls_back_to_auto_for_unknown_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SOATVAN_GPU_OFFLOAD", raising=False)
    assert offload_mode() == "auto"
    monkeypatch.setenv("SOATVAN_GPU_OFFLOAD", "OFF")
    assert offload_mode() == "off"
    monkeypatch.setenv("SOATVAN_GPU_OFFLOAD", "yes-please")
    assert offload_mode() == "auto"


CUDA_BUILD = build_id(backend_report(FakeLlama()))


def test_guard_needs_two_unfinished_loads_before_it_blocks(state_dir: Path) -> None:
    guard = OffloadGuard(state_dir, CUDA_BUILD)
    assert guard.blocked_reason() is None

    # One marker left behind is ambiguous: closing the window mid-load kills the
    # sidecar and leaves exactly this trace, so the GPU still gets a second try.
    guard.begin()
    assert OffloadGuard(state_dir, CUDA_BUILD).blocked_reason() is None

    OffloadGuard(state_dir, CUDA_BUILD).begin()
    assert "crashed" in (OffloadGuard(state_dir, CUDA_BUILD).blocked_reason() or "")

    # Getting through a load clears the tally.
    guard.succeeded()
    assert OffloadGuard(state_dir, CUDA_BUILD).blocked_reason() is None
    guard.begin()
    assert OffloadGuard(state_dir, CUDA_BUILD).blocked_reason() is None

    # A caught failure is unambiguous and blocks on the first one.
    guard.failed("CUDA error: out of memory")
    assert OffloadGuard(state_dir, CUDA_BUILD).blocked_reason() == "CUDA error: out of memory"

    guard.clear()
    assert OffloadGuard(state_dir, CUDA_BUILD).blocked_reason() is None


def test_guard_ignores_a_marker_written_by_another_build(state_dir: Path) -> None:
    """A new portable release must not inherit the old one's verdict."""
    OffloadGuard(state_dir, "0.1.0:deadbeefcafe").failed("CUDA error: out of memory")
    assert OffloadGuard(state_dir, CUDA_BUILD).blocked_reason() is None
    # …and the build that did write it still honours it.
    assert OffloadGuard(state_dir, "0.1.0:deadbeefcafe").blocked_reason() is not None


def test_build_id_changes_with_the_native_library(state_dir: Path) -> None:
    cuda = build_id(backend_report(FakeLlama()))
    cpu = build_id(backend_report(FakeLlama(system_info="CPU : AVX2 = 1 | ")))
    assert cuda != cpu
    assert cuda.startswith(f"{engine_version}:")


def test_guard_ignores_a_corrupt_marker(state_dir: Path) -> None:
    (state_dir / "gpu-offload.json").write_text("{not json", encoding="utf-8")
    assert OffloadGuard(state_dir, CUDA_BUILD).blocked_reason() is None


def test_guard_records_the_last_decision_for_the_ui(state_dir: Path) -> None:
    guard = OffloadGuard(state_dir, CUDA_BUILD)
    assert guard.last_decision() is None

    offload_allowed(guard, backend_report(FakeLlama()))
    decision = guard.last_decision()
    assert decision is not None
    assert decision["offload"] is True
    assert decision["blocked"] is False

    guard.failed("CUDA error: out of memory")
    offload_allowed(guard, backend_report(FakeLlama()))
    blocked = OffloadGuard(state_dir).last_decision()
    assert blocked is not None
    assert blocked["offload"] is False
    assert blocked["blocked"] is True
    assert "out of memory" in blocked["reason"]


def test_offload_is_refused_when_blocked_and_retried_only_on_force(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = backend_report(FakeLlama())
    guard = OffloadGuard(state_dir, build_id(report))
    assert offload_allowed(guard, report)[0] is True

    guard.failed("CUDA error: out of memory")
    allowed, reason = offload_allowed(guard, report)
    assert allowed is False
    assert "out of memory" in reason

    monkeypatch.setenv("SOATVAN_GPU_OFFLOAD", "force")
    assert offload_allowed(guard, report)[0] is True
    # force clears the block for later runs too
    monkeypatch.delenv("SOATVAN_GPU_OFFLOAD")
    assert offload_allowed(guard, report)[0] is True


def test_offload_is_refused_without_a_device_or_when_switched_off(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    guard = OffloadGuard(state_dir, CUDA_BUILD)
    cpu_only = backend_report(
        FakeLlama(supports_offload=False, system_info="CPU : AVX2 = 1 | ")
    )
    allowed, reason = offload_allowed(guard, cpu_only)
    assert allowed is False
    assert "no GPU device" in reason

    monkeypatch.setenv("SOATVAN_GPU_OFFLOAD", "off")
    allowed, reason = offload_allowed(guard, backend_report(FakeLlama()))
    assert allowed is False
    assert "off" in reason


def test_runtime_factory_offloads_every_layer_when_a_gpu_is_present(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    llama = FakeLlama()
    monkeypatch.setattr(classifier_module, "import_module", lambda _: llama)

    _default_runtime_factory(tmp_path / "model.gguf", 4096, 0)

    assert llama.requested_gpu_layers == [-1]
    assert OffloadGuard(state_dir, CUDA_BUILD).blocked_reason() is None


def test_runtime_factory_falls_back_to_cpu_and_remembers_the_failure(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    llama = FakeLlama(gpu_load_error=RuntimeError("CUDA error: out of memory"))
    monkeypatch.setattr(classifier_module, "import_module", lambda _: llama)

    _default_runtime_factory(tmp_path / "model.gguf", 4096, 0)
    assert llama.requested_gpu_layers == [-1, 0]

    # The next load skips the GPU entirely instead of paying for the failure again.
    again = FakeLlama(gpu_load_error=RuntimeError("CUDA error: out of memory"))
    monkeypatch.setattr(classifier_module, "import_module", lambda _: again)
    _default_runtime_factory(tmp_path / "model.gguf", 4096, 0)
    assert again.requested_gpu_layers == [0]


def test_runtime_factory_reports_a_load_failure_that_cpu_cannot_rescue(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Broken(FakeLlama):
        def Llama(self, **kwargs: Any) -> Any:  # noqa: N802 - mirrors the native name
            self.requested_gpu_layers.append(int(kwargs["n_gpu_layers"]))
            self.requested_context_sizes.append(int(kwargs["n_ctx"]))
            raise RuntimeError("model file is corrupt")

    llama = Broken()
    monkeypatch.setattr(classifier_module, "import_module", lambda _: llama)

    with pytest.raises(ModelLoadFailed):
        _default_runtime_factory(tmp_path / "model.gguf", 4096, 0)
    # GPU, then CPU, then one CPU retry at half the context — the crash guard
    # has already ruled out offload by the time the context is shrunk.
    assert llama.requested_gpu_layers == [-1, 0, 0]
    assert llama.requested_context_sizes == [4096, 4096, 2048]
