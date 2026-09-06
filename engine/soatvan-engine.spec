from importlib.util import find_spec

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

binaries = collect_dynamic_libs("lxml")
datas = []
hiddenimports = []
if find_spec("llama_cpp") is not None:
    llama_datas, llama_binaries, llama_hiddenimports = collect_all("llama_cpp")
    datas += llama_datas
    binaries += llama_binaries
    hiddenimports += llama_hiddenimports
if find_spec("torch") is not None:
    torch_datas, torch_binaries, torch_hiddenimports = collect_all("torch")
    datas += torch_datas
    binaries += torch_binaries
    hiddenimports += torch_hiddenimports
if find_spec("transformers") is not None:
    tf_datas, tf_binaries, tf_hiddenimports = collect_all("transformers")
    datas += tf_datas
    binaries += tf_binaries
    hiddenimports += tf_hiddenimports
a = Analysis(["src/soatvan/entrypoints/sidecar.py"], pathex=["src"], binaries=binaries, hiddenimports=hiddenimports, datas=datas)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="soatvan-engine", console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="soatvan-engine")
