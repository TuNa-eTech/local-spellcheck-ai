from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = collect_dynamic_libs("lxml")
a = Analysis(["src/soatvan/entrypoints/sidecar.py"], pathex=["src"], binaries=binaries, hiddenimports=[], datas=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="soatvan-engine", console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="soatvan-engine")

