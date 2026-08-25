# SoátVăn Desktop

Ứng dụng Windows kiểm tra DOCX hoàn toàn cục bộ theo workflow:

`Chọn file → Chuẩn bị rà soát → Xử lý → Mở file output`

MVP không preview, không duyệt từng finding và không tự sửa text. File output giữ nguyên nội dung, bôi vàng cảnh báo và thêm Word comment; file nguồn luôn bất biến.

## Cách xử lý

**Bộ kiểm tra cơ bản là cố định và luôn chạy**; giao diện không có preset, checkbox nhóm quy tắc, từ điển người dùng hoặc danh sách từ bỏ qua. Pipeline có ba nhánh kỹ thuật, suy ra từ trạng thái AI và lựa chọn rà soát sâu:

- **Kiểm tra cơ bản** (`use_model=false`): chạy bộ luật cố định; không nạp model.
- **AI filter** (`use_model=true`, `full_review=false`): LLM chỉ giữ hoặc loại candidate do rule engine tạo.
- **AI full review** (`use_model=true`, `full_review=true`): mọi block được hỗ trợ đều đi qua bộ chia chunk theo token; trong cùng một lượt cho mỗi chunk, LLM vừa phân loại candidate vừa có thể tìm thêm lỗi mà rule bỏ sót.

Settings chỉ có **Quy tắc riêng** và **AI cục bộ**. Mỗi quy tắc riêng là một đoạn prompt text có thể thêm, sửa hoặc xoá. Tổng text lưu tối đa 4.000 ký tự; khi AI bật và sẵn sàng, các mục được ghép theo thứ tự bằng dòng trống thành một context chung. Khi AI tắt, các mục vẫn được lưu nhưng không ảnh hưởng bộ kiểm tra cơ bản.

Full review không đưa file DOCX nhị phân hoặc toàn bộ nội dung vào một prompt. Engine giữ anchor OOXML, gửi lần lượt các chunk văn bản có giới hạn context và chỉ chấp nhận finding khớp chính xác `paragraph_id + source_text + occurrence_index`. Kết quả full review kèm coverage; nếu một phần chunk timeout/lỗi thì trạng thái là `partial`, còn nếu không chunk nào rà thành công thì job thất bại. Cả hai trường hợp đều không được diễn giải thành “không có lỗi”. Phạm vi hiện tại vẫn là main body và bảng; header/footer/textbox/footnote được giữ nguyên nhưng chưa được soát.

## Cấu trúc

- `apps/desktop`: Tauri 2, Rust host và Vanilla TypeScript/Vite UI.
- `engine`: Python 3.12 rule engine, OOXML, kho SQLite cho quy tắc riêng và NDJSON sidecar.
- `contracts`: JSON Schema cho IPC, finding và gói model.
- `docs`: PRD, technical design và mockup HTML.

Dependency đi vào trong: `checking` và `workflow` không import Tauri, SQLite, ZIP hay lxml. Adapter OOXML/SQLite/NDJSON nằm bên ngoài use case.

## Chạy development

Yêu cầu: Node 24+, Rust stable hiện hành, `uv`, Python 3.12 do `uv` quản lý.

```sh
uv sync --project engine --extra dev --extra model
npm install --prefix apps/desktop
npm run tauri -- dev
```

Trong browser-only mode (`npm run dev`), UI dùng dữ liệu demo. Khi chạy Tauri, Rust khởi động Python sidecar thường trú bằng `uv` và handshake protocol v1.

## Kiểm tra

```sh
uv run --project engine pytest --cov=soatvan --cov-fail-under=85
uv run --project engine ruff check engine
uv run --project engine mypy --config-file engine/pyproject.toml
npm test --prefix apps/desktop
npm run build --prefix apps/desktop
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml
cargo clippy --all-targets --manifest-path apps/desktop/src-tauri/Cargo.toml -- -D warnings
```

Mọi gate phụ thuộc Windows chạy trong GitHub Actions: sidecar PyInstaller không có Python trên `PATH`, đường dẫn Unicode/dài, cancel, Open XML SDK, NSIS standard-user, Microsoft Defender, zero-egress khi khởi động và Windows Job Object không để process mồ côi. Xem ma trận tại [docs/m0-m1-acceptance.md](docs/m0-m1-acceptance.md).

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

Build cá nhân dùng Windows self-signed certificate (`CN=SoatVan Personal Use`) và macOS ad-hoc identity `-`. Đây không phải chữ ký tin cậy công khai; xem giới hạn và cách xác minh trong [docs/build.md](docs/build.md).

CI Windows build PyInstaller `onedir`, copy toàn bộ onedir vào Tauri resources, rồi build NSIS với WebView2 `offlineInstaller`. Model không nằm trong installer.

Provisioning qua mạng chỉ được compile khi có đủ ba biến:

- `SOATVAN_MODEL_ENDPOINT`: URL HTTPS chính xác.
- `SOATVAN_MODEL_ALLOWLIST`: danh sách hostname phân cách bằng dấu phẩy.
- `SOATVAN_MODEL_PUBLIC_KEY`: Ed25519 public key base64.

Không có cấu hình trên, lệnh tải fail-closed với `MODEL_NOT_CONFIGURED`; nhập gói `.svmodel` vẫn kiểm tra manifest, size, SHA-256 và chữ ký trước khi đổi active directory atomically.

## Trạng thái milestone

- M0: source layout, protocol, persistent sidecar, crash/error boundary, safe DOCX ZIP validation, PyInstaller `onedir`, advanced golden DOCX và Windows Job Object đã được tự động hoá trong workflow `verify`.
- M1: workflow bốn bước, metadata từ/trang best-effort, bộ kiểm tra cơ bản cố định, technical/confusion/capitalization/conservative-syllable rules, NFC source mapping, annotation-only DOCX, atomic no-clobber output và no-finding đã có unit/property/security/contract/UI/performance tests.
- M2: CRUD quy tắc riêng, `llama-cpp-python` classifier và nhánh full review tùy chọn theo chunk, output có cấu trúc, anchor/coverage fail-closed, timeout/cancel, signed capability gate, import/download resume, crash recovery, smoke-load/rollback và toggle giải phóng runtime có trong source. Model chỉ `ready` khi package hợp lệ và smoke-load thành công; model chỉ được full review khi manifest ký số phê duyệt capability này. Chưa có model được phê duyệt thì ứng dụng tiếp tục bằng bộ kiểm tra cơ bản cố định.
- M3: Windows CI/NSIS/WebView2 offline config, personal signing và Defender gate đã có. Developer ID/notarization và certificate công khai không nằm trong M0/M1.

Corpus regression tổng hợp có 20 trường hợp và gate precision/recall tự động cho AI filter. Quality gate trên 20 DOCX thật vẫn cần bộ tài liệu ẩn danh và ground truth do người dùng duyệt; đây là evidence đầu vào, không được thay thế bằng dữ liệu giả. Full review phải có corpus/gate riêng đo discovery, coverage, latency và RAM trước khi được coi là capability phát hành.
