# SoátVăn Desktop

Ứng dụng Windows kiểm tra DOCX hoàn toàn cục bộ theo workflow:

`Chọn file → Chọn preset → Xử lý → Mở file output`

MVP không preview, không duyệt từng finding và không tự sửa text. File output giữ nguyên nội dung, bôi vàng cảnh báo và thêm Word comment; file nguồn luôn bất biến.

## Cấu trúc

- `apps/desktop`: Tauri 2, Rust host và Vanilla TypeScript/Vite UI.
- `engine`: Python 3.12 rule engine, OOXML, SQLite/CSV và NDJSON sidecar.
- `contracts`: JSON Schema cho IPC, finding và gói model.
- `docs`: PRD, technical design và mockup HTML.

Dependency đi vào trong: `checking` và `workflow` không import Tauri, SQLite, ZIP hay lxml. Adapter OOXML/SQLite/NDJSON nằm bên ngoài use case.

## Chạy development

Yêu cầu: Node 24+, Rust stable hiện hành, `uv`, Python 3.12 do `uv` quản lý.

```sh
uv sync --project engine --extra dev
npm install --prefix apps/desktop
npm run tauri -- dev
```

Trong browser-only mode (`npm run dev`), UI dùng dữ liệu demo. Khi chạy Tauri, Rust khởi động Python sidecar thường trú bằng `uv` và handshake protocol v1.

## Kiểm tra

```sh
uv run --project engine pytest
uv run --project engine ruff check engine
uv run --project engine mypy --config-file engine/pyproject.toml
npm run build --prefix apps/desktop
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml
```

## Build Windows

Build bộ cài NSIS `.exe` trên Windows:

```powershell
npm run build:windows
```

Build file `.dmg` trên macOS:

```sh
npm run build:macos
```

Hai script tự đồng bộ dependency, build Python sidecar bằng PyInstaller `onedir`, copy sidecar vào Tauri resources và đóng gói ứng dụng. Không thể dùng sidecar build trên một hệ điều hành hoặc kiến trúc CPU cho hệ điều hành khác.

Xem yêu cầu môi trường, vị trí artifact và hướng dẫn ký ứng dụng tại [docs/build.md](docs/build.md).

Push tag `vMAJOR.MINOR.PATCH` sẽ chạy GitHub Actions để kiểm tra source, build Windows x64 cùng macOS arm64/Intel, tạo checksum và publish GitHub Release tự động.

CI Windows build PyInstaller `onedir`, copy toàn bộ onedir vào Tauri resources, rồi build NSIS với WebView2 `offlineInstaller`. Model không nằm trong installer.

Provisioning qua mạng chỉ được compile khi có đủ ba biến:

- `SOATVAN_MODEL_ENDPOINT`: URL HTTPS chính xác.
- `SOATVAN_MODEL_ALLOWLIST`: danh sách hostname phân cách bằng dấu phẩy.
- `SOATVAN_MODEL_PUBLIC_KEY`: Ed25519 public key base64.

Không có cấu hình trên, lệnh tải fail-closed với `MODEL_NOT_CONFIGURED`; nhập gói `.svmodel` vẫn kiểm tra manifest, size, SHA-256 và chữ ký trước khi đổi active directory atomically.

## Trạng thái milestone

- M0: source layout, protocol, persistent sidecar, crash/error boundary, safe DOCX ZIP validation, PyInstaller spec và Windows Job Object đã có. Việc chứng nhận “Word không repair” trên máy Windows sạch cần chạy acceptance corpus.
- M1: workflow, 3 preset, technical/confusion/capitalization rules, NFC source mapping, annotation-only DOCX, collision-safe output, no-finding, Settings và SQLite/CSV đã có. Bộ âm tiết tiếng Việt production và quality corpus vẫn là data gate, chưa được giả lập bằng một wordlist nhỏ thiếu bằng chứng.
- M2: signed provisioning/import/download source đã có; model được giữ ở trạng thái `installed` cho đến khi `llama.cpp` classifier và benchmark 8/16 GB vượt gate. Prompt vì thế vẫn bị khóa fail-closed.
- M3: Windows CI/NSIS/WebView2 offline config đã có. Authenticode, Defender và Word 2016/2019/365 cần certificate và runner/VM nghiệm thu thực tế.

Quality gate recall/precision chưa thể tuyên bố đạt cho đến khi có 20 DOCX ẩn danh và ground truth do khách hàng duyệt.
