import os
import sys
from importlib.util import find_spec
from pathlib import Path, PurePath

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

binaries = collect_dynamic_libs("lxml")
datas = []
hiddenimports = []

# The CUDA backend imports these by name; without them beside `ggml-cuda.dll`
# the whole llama.cpp stack fails to load, not just the GPU path.
CUDA_REDISTRIBUTABLES = ("cudart64_*.dll", "cublas64_*.dll", "cublasLt64_*.dll")


def _bundle(package: str) -> None:
    """Fold a package's data files, shared libraries and submodules into the build."""
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package)
    datas.extend(pkg_datas)
    binaries.extend(pkg_binaries)
    hiddenimports.extend(pkg_hiddenimports)


def _bundle_cuda_runtime() -> None:
    """Ship the CUDA runtime next to a CUDA-enabled llama.cpp.

    `scripts/build-llama-cuda.ps1` normally stages these into the package
    itself, where `collect_all` already finds them; this covers a locally built
    wheel installed by hand, by falling back to the toolkit the build machine
    has. A CUDA build with no redistributable anywhere is a broken package, so
    it fails the build rather than shipping an engine that cannot start.
    """
    spec = find_spec("llama_cpp")
    if sys.platform != "win32" or spec is None or spec.origin is None:
        return
    library_dir = Path(spec.origin).parent / "lib"
    if not (library_dir / "ggml-cuda.dll").is_file():
        return  # CPU-only build: nothing to carry.

    search_dirs = [library_dir]
    staged = os.environ.get("SOATVAN_CUDA_RUNTIME_DIR")
    if staged:
        search_dirs.append(Path(staged))
    toolkit = os.environ.get("CUDA_PATH")
    if toolkit:
        search_dirs.append(Path(toolkit) / "bin")

    for pattern in CUDA_REDISTRIBUTABLES:
        found = next(
            (match for directory in search_dirs for match in sorted(directory.glob(pattern))),
            None,
        )
        if found is None:
            raise SystemExit(
                f"llama.cpp was built with CUDA but {pattern} is missing from "
                f"{', '.join(str(directory) for directory in search_dirs)}. "
                "Run scripts/build-llama-cuda.ps1 or set SOATVAN_CUDA_RUNTIME_DIR."
            )
        if found.parent == library_dir:
            continue  # already inside the package, so collect_all has it
        binaries.append((str(found), "llama_cpp/lib"))


# llama.cpp candidate-filter / full-review classifier (`--extra model`).
if find_spec("llama_cpp") is not None:
    _bundle("llama_cpp")
    _bundle_cuda_runtime()

# seq2seq speller (`--extra seq2seq`). torch and transformers carry their own
# PyInstaller hooks, but `collect_all("transformers")` is still needed for the
# model/tokenizer submodules the hook does not gather.
if find_spec("torch") is not None:
    _bundle("torch")
if find_spec("transformers") is not None:
    _bundle("transformers")

    # The tokenizer stack that BARTpho / ViT5 need has NO PyInstaller hooks, and
    # `AutoTokenizer.from_pretrained` reaches SentencePiece and protobuf through
    # runtime indirection the frozen import graph cannot follow. Without an
    # explicit collect, the seq2seq pass raises inside the packaged sidecar and
    # is silently skipped, so the user gets no spelling findings and no error.
    for _package in ("sentencepiece", "tokenizers", "safetensors", "huggingface_hub"):
        if find_spec(_package) is not None:
            _bundle(_package)

    if find_spec("google.protobuf") is not None:
        _bundle("google.protobuf")
        # protobuf selects its C / pure-python backend at import time via
        # importlib; name both extension modules plus the selector so PyInstaller
        # keeps whichever this build environment actually has.
        hiddenimports += [
            "google._upb._message",
            "google.protobuf.internal._api_implementation",
            "google.protobuf.pyext._message",
        ]


def _deduplicate_cuda_runtime(collected):
    """Drop the second copy of the CUDA runtime PyInstaller finds by itself.

    Scanning `ggml-cuda.dll` resolves the same DLLs through the build machine's
    PATH and stages them at the bundle root, which would add ~650 MB on top of
    the copy that already sits beside the backend. The root copies only go when
    the `llama_cpp/lib` ones are there to replace them.
    """
    import fnmatch

    library_destinations = {
        entry[0]
        for entry in collected
        if PurePath(entry[0]).parent.as_posix() == "llama_cpp/lib"
        and any(fnmatch.fnmatch(PurePath(entry[0]).name, p) for p in CUDA_REDISTRIBUTABLES)
    }
    library_names = {PurePath(destination).name for destination in library_destinations}
    return [
        entry
        for entry in collected
        if entry[0] in library_destinations or PurePath(entry[0]).name not in library_names
    ]


a = Analysis(
    ["src/soatvan/entrypoints/sidecar.py"],
    pathex=["src"],
    binaries=binaries,
    hiddenimports=hiddenimports,
    datas=datas,
)
a.binaries = _deduplicate_cuda_runtime(a.binaries)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="soatvan-engine", console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="soatvan-engine")
