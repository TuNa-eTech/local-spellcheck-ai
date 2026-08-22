# Build SoátVăn Desktop

## Nguyên tắc

SoátVăn gồm Tauri host và Python sidecar được đóng gói bằng PyInstaller `onedir`. Cả hai chứa mã native, vì vậy phải build trên đúng hệ điều hành và kiến trúc đích:

- Windows x64 build installer Windows x64.
- macOS Apple Silicon build DMG arm64.
- macOS Intel build DMG x86_64.

Không copy sidecar đã build giữa Windows, macOS, arm64 và x86_64.

## Yêu cầu chung

- Git.
- Node.js 24 trở lên và npm.
- Rust 1.98.0 qua `rustup`; repository đã pin phiên bản bằng `rust-toolchain.toml`.
- `uv` và Python 3.12 do `uv` quản lý.
- Dependency hệ thống của Tauri 2 cho nền tảng đang build.

Chạy script từ bất kỳ thư mục nào trong shell đều được; script tự xác định repository root. Dependency Python được cài từ `engine/uv.lock`, dependency frontend được cài bằng `npm ci` từ lockfile.

Nếu chưa có toolchain đã pin:

```sh
rustup toolchain install 1.98.0 --profile minimal --component clippy,rustfmt
```

## Build Windows `.exe`

Chạy trên Windows bằng PowerShell:

```powershell
npm run build:windows
```

Hoặc gọi trực tiếp:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-windows.ps1
```

Artifact NSIS nằm tại:

```text
apps/desktop/src-tauri/target/release/bundle/nsis/*.exe
```

Installer dùng WebView2 `offlineInstaller`, vì vậy kích thước lớn hơn nhưng không cần tải WebView2 trong lúc cài đặt.

## Build macOS `.dmg`

Chạy trên macOS:

```sh
npm run build:macos
```

Hoặc gọi trực tiếp:

```sh
./scripts/build-macos.sh
```

Artifact nằm tại:

```text
apps/desktop/src-tauri/target/release/bundle/dmg/*.dmg
```

Script build đúng kiến trúc của máy đang chạy. Để phát hành cho cả Apple Silicon và Intel, nên chạy hai job macOS riêng và phát hành hai DMG có tên kiến trúc rõ ràng. Universal binary cần hợp nhất cả Tauri executable lẫn toàn bộ native library trong PyInstaller sidecar, nên không được tạo chỉ bằng cách đổi target của Tauri.

## Model và cấu hình build

Model không được đóng trong installer. Tải model chỉ được compile khi có đủ các biến môi trường sau:

- `SOATVAN_MODEL_ENDPOINT`: URL HTTPS chính xác.
- `SOATVAN_MODEL_ALLOWLIST`: hostname được phép, phân cách bằng dấu phẩy.
- `SOATVAN_MODEL_PUBLIC_KEY`: Ed25519 public key dạng base64.

Nếu không có đủ cấu hình, ứng dụng vẫn build và chạy rule-only; chức năng tải model fail-closed. Import gói model offline vẫn yêu cầu manifest, checksum và chữ ký hợp lệ.

## Ký bản phát hành

Script local không tự ký bằng certificate:

- Windows: cần Authenticode-sign ứng dụng, sidecar, DLL và NSIS installer trước khi phát hành.
- macOS: cần Apple Developer ID, hardened runtime, code signing và notarization để tránh cảnh báo Gatekeeper trên máy người dùng.

CI release nên quản lý certificate và secret trong secret store của runner, không commit vào repository.

## GitHub Release tự động

Workflow `.github/workflows/release.yml` chạy khi push tag bắt đầu bằng `v`. Tag hợp lệ phải có dạng `vMAJOR.MINOR.PATCH`, trỏ tới commit thuộc nhánh `main` và khớp version trong ba manifest:

- `apps/desktop/package.json`.
- `apps/desktop/src-tauri/tauri.conf.json`.
- `engine/pyproject.toml`.

Ví dụ phát hành version `0.1.0`:

```sh
git tag -a v0.1.0 -m "SoátVăn v0.1.0"
git push origin v0.1.0
```

Pipeline chạy test trước, sau đó build song song:

- Windows x64 NSIS `.exe`.
- macOS arm64 `.dmg`.
- macOS Intel x64 `.dmg`.

Khi tất cả job thành công, pipeline tạo `SHA256SUMS.txt`, sinh release notes và publish GitHub Release gắn với tag đã tồn tại. Nếu một job thất bại, Release không được tạo; sửa source, tăng patch version và tạo tag mới. Không di chuyển hoặc tái sử dụng tag đã phát hành.

Các artifact hiện chưa được ký nếu repository chưa cấu hình certificate tương ứng. Không nên phân phối bản unsigned cho người dùng cuối ngoài môi trường kiểm thử.

## Dữ liệu tạm khi build

PyInstaller tạo `engine/build` và `engine/dist`. Script đồng bộ bản `onedir` vào `apps/desktop/src-tauri/resources/engine`; các nội dung sinh ra này đã được `.gitignore` loại trừ. File `README.txt` trong thư mục resource được giữ lại làm placeholder của source tree.
