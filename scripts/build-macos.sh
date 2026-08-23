#!/usr/bin/env bash

set -Eeuo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Loi: build DMG phai chay tren macOS." >&2
  exit 1
fi

for command_name in uv npm cargo rsync codesign hdiutil; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Loi: khong tim thay lenh '${command_name}'." >&2
    exit 1
  fi
done

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
engine_resource_dir="${repo_root}/apps/desktop/src-tauri/resources/engine"
engine_dist_dir="${repo_root}/engine/dist/soatvan-engine"
dmg_dir="${repo_root}/apps/desktop/src-tauri/target/release/bundle/dmg"
app_bundle="${repo_root}/apps/desktop/src-tauri/target/release/bundle/macos/SoátVăn.app"

cd "${repo_root}"

echo "[1/4] Dong bo dependency Python"
uv sync --project engine --extra dev --locked

if [[ "${SOATVAN_PYINSTALLER_CACHE_HIT:-false}" == "true" && -d "engine/build/soatvan-engine" ]]; then
  find engine/build/soatvan-engine -exec touch {} +
fi

echo "[2/4] Build Python sidecar onedir"
(
  cd engine
  uv run pyinstaller --noconfirm soatvan-engine.spec
)

if [[ ! -x "${engine_dist_dir}/soatvan-engine" ]]; then
  echo "Loi: PyInstaller khong tao executable ${engine_dist_dir}/soatvan-engine." >&2
  exit 1
fi

echo "[3/4] Dong bo sidecar va dependency frontend"
mkdir -p "${engine_resource_dir}"
rsync -a --delete --exclude README.txt "${engine_dist_dir}/" "${engine_resource_dir}/"
npm --prefix apps/desktop ci

echo "[4/4] Build Tauri DMG"
npm --prefix apps/desktop run tauri -- build --bundles app,dmg

if [[ ! -d "${app_bundle}" ]]; then
  echo "Loi: khong tim thay app bundle ${app_bundle}." >&2
  exit 1
fi

codesign --verify --deep --strict --verbose=2 "${app_bundle}"

if ! find "${dmg_dir}" -maxdepth 1 -type f -name '*.dmg' -print -quit | grep -q .; then
  echo "Loi: khong tim thay DMG trong ${dmg_dir}." >&2
  exit 1
fi

while IFS= read -r dmg_path; do
  hdiutil verify "${dmg_path}"
done < <(find "${dmg_dir}" -maxdepth 1 -type f -name '*.dmg' -print)

echo "Build thanh cong:"
find "${dmg_dir}" -maxdepth 1 -type f -name '*.dmg' -print
