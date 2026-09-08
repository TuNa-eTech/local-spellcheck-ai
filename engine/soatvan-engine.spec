from importlib.util import find_spec

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

binaries = collect_dynamic_libs("lxml")
datas = []
hiddenimports = []


def _bundle(package: str) -> None:
    """Fold a package's data files, shared libraries and submodules into the build."""
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package)
    datas.extend(pkg_datas)
    binaries.extend(pkg_binaries)
    hiddenimports.extend(pkg_hiddenimports)


# llama.cpp candidate-filter / full-review classifier (`--extra model`).
if find_spec("llama_cpp") is not None:
    _bundle("llama_cpp")

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

a = Analysis(
    ["src/soatvan/entrypoints/sidecar.py"],
    pathex=["src"],
    binaries=binaries,
    hiddenimports=hiddenimports,
    datas=datas,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="soatvan-engine", console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="soatvan-engine")
