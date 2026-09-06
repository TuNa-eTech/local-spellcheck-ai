# SoatVan-itowf Desktop

Ứng dụng Windows kiểm tra DOCX hoàn toàn cục bộ theo workflow:

`Chọn file → Chuẩn bị rà soát → Xử lý → Mở file output`

MVP không preview, không duyệt từng finding và không tự sửa text. File output giữ nguyên nội dung, bôi vàng cảnh báo và thêm Word comment; file nguồn luôn bất biến.

## Cách xử lý

Khi model sẵn sàng, giao diện mặc định chọn **Chỉ dùng AI để rà soát**. Prompt tiếng Việt tích hợp yêu cầu model tự tìm lỗi trong từng block. Người dùng có thể bật riêng **Bổ sung cảnh báo từ bộ quy tắc code**; AI vẫn rà toàn văn, còn rule engine chỉ cộng thêm các finding xác định được bằng logic cố định. Tùy chọn này mặc định tắt. Hai nhánh cũ vẫn được giữ để tương thích khi AI/full review bị tắt:

- **Kiểm tra cơ bản** (`use_model=false`): chạy bộ luật cố định; không nạp model.
- **AI filter** (`use_model=true`, `full_review=false`): LLM chỉ giữ hoặc loại candidate do rule engine tạo.
- **AI full review** (`use_model=true`, `full_review=true`, mặc định khi model hỗ trợ): mọi block được hỗ trợ đều đi qua bộ chia chunk theo token và LLM tự tạo discovery. Với `include_rule_findings=false` đây là LLM-only; với `include_rule_findings=true`, rule engine chạy bổ sung nhưng AI vẫn quyết định chính: candidate bị `drop` với độ tin cậy đủ cao sẽ bị loại, còn kết quả thiếu/không chắc chắn được giữ theo hướng an toàn.

Settings là một trang trong ứng dụng, không phải modal/dialog, với ba mục:

- **Prompt**: bố cục master-detail để xem, thêm, sửa, xoá và hoàn tác xoá các prompt quy tắc riêng. Mỗi mục gồm một tiêu đề bắt buộc (tối đa 80 ký tự, chỉ dùng để nhận diện và không gửi cho model), một đoạn prompt text và cờ **chọn sẵn**; tổng text prompt lưu tối đa 4.000 ký tự. Ở bước Chuẩn bị rà soát, người dùng tick chọn prompt theo tiêu đề; các mục được chọn ghép theo thứ tự bằng dòng trống thành một context chung khi AI bật và sẵn sàng. Prompt chỉ bổ sung thuật ngữ/tiêu chí kiểm tra, không được thay đổi schema output hoặc yêu cầu model trả cả câu/đoạn làm vị trí lỗi. Khi AI tắt, các mục vẫn được lưu nhưng không ảnh hưởng bộ kiểm tra cơ bản.
- **Quy tắc rà soát**: inventory chỉ đọc của bộ kiểm tra cố định. Mục này không có preset, từ điển, danh sách bỏ qua hoặc công tắc bật/tắt detector; tùy chọn chạy bổ sung bộ quy tắc code vẫn nằm ở bước Chuẩn bị rà soát.
- **AI cục bộ**: xem trạng thái và quản lý vòng đời model trên máy, gồm nhập gói từ tệp có sẵn, huỷ thao tác, kích hoạt/tắt runtime và gỡ model theo release profile. Ứng dụng không tải model qua mạng.

Khi mở Settings hoặc chuyển mục, focus đi tới heading của trang/mục mới; nút quay lại khôi phục focus cho đúng control đã mở Settings. `Ctrl+O` chỉ hoạt động trong view rà soát và bị bỏ qua trong Settings; Settings không có hành vi đóng bằng `Esc` như dialog.

Full review không đưa file DOCX nhị phân hoặc toàn bộ nội dung vào một prompt. Engine giữ anchor OOXML, gửi tuần tự các chunk văn bản có giới hạn context và chỉ chấp nhận finding khớp chính xác `paragraph_id + source_text + occurrence_index`. `review_chunk_tokens` chỉ giới hạn text đích của tài liệu; system/chat template, quy tắc riêng, output reserve và safety margin được tính thành các ngân sách riêng trước khi chia chunk. Nếu riêng prompt đã không còn đủ chỗ cho một chunk hữu dụng, engine dừng sớm với lỗi prompt/context cụ thể thay vì tiếp tục chia đến từng ký tự. GGUF Gemma 4 nhập cục bộ dùng context 4.096 token; mức này nhỏ hơn rất nhiều so với context tối đa của model để giữ mức RAM thực tế trên máy yếu. Context 4K dành tối đa 2.048 token cho JSON discovery để các chunk có nhiều lỗi không bị cắt output; context 2K cũ vẫn giữ mức 768 token. Trước khi xuất, quality gate loại đề xuất no-op, câu/đoạn bị dùng làm replacement cho một lỗi ngắn và các rewrite không thể thu về một edit cục bộ an toàn; bản sửa có context trùng khớp được thu về đúng từ/cụm từ sai. Runtime tự dùng GPU offload khi backend hỗ trợ và tự fallback CPU; model local chờ tối đa 600 giây cho mỗi lần gọi. Chunk lỗi được chia đôi và thử lại tuần tự tối đa hai cấp. Kết quả kèm coverage cùng số timeout/JSON lỗi/retry; nếu vẫn còn phần lỗi thì trạng thái là `partial`, còn nếu không phần nào rà thành công thì job thất bại. Cả hai trường hợp đều không được diễn giải thành “không có lỗi”. Phạm vi hiện tại vẫn là main body và bảng; header/footer/textbox/footnote được giữ nguyên nhưng chưa được soát.

Trước mỗi lần gọi hoặc retry, engine phát lại tín hiệu hoạt động mà không tăng coverage giả. Watchdog desktop chờ 1.020 giây không có hoạt động, lớn hơn timeout tối đa 900 giây/lần gọi mà manifest cho phép, nên máy yếu có thể hoàn tất hoặc chuyển sang retry tuần tự trước khi host can thiệp.

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

Trong browser-only mode (`npm run dev` hoặc mở trực tiếp `http://localhost:1420`), UI dùng dữ liệu demo. Khi chạy `npm run tauri -- dev`, hãy thao tác trong cửa sổ desktop **SoatVan-itowf** do Tauri tự mở, không mở URL Vite trong trình duyệt. Cửa sổ Tauri dùng Rust host để khởi động Python sidecar thường trú bằng `uv` và handshake protocol v1.

Ở dev build, terminal hiển thị lỗi Rust host với prefix `[soatvan-host]` và chuyển tiếp `stderr` của Python/model với prefix `[soatvan-sidecar]`. Sidecar ghi mã lỗi, loại exception và traceback khi job thất bại. Release build không bật các log chẩn đoán này; `stdout` của sidecar vẫn chỉ dành cho NDJSON protocol.

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

Ứng dụng không có đường tải model qua mạng. Model chỉ được đưa vào máy bằng lệnh nhập gói: `.svmodel` đã ký được kiểm manifest, size, SHA-256 và chữ ký Ed25519 trước khi đổi active directory atomically, còn `.gguf` nhập trực tiếp được đánh dấu `local_unverified`. Xác minh chữ ký gói ký số cần biến compile-time `SOATVAN_MODEL_PUBLIC_KEY` (Ed25519 public key base64); không có biến này, nhập gói ký số fail-closed với `MODEL_NOT_CONFIGURED`.

## Trạng thái milestone

- M0: source layout, protocol, persistent sidecar, crash/error boundary, safe DOCX ZIP validation, PyInstaller `onedir`, advanced golden DOCX và Windows Job Object đã được tự động hoá trong workflow `verify`.
- M1: workflow bốn bước, metadata từ/trang best-effort, bộ kiểm tra cơ bản cố định, technical/confusion/capitalization/conservative-syllable rules, NFC source mapping, annotation-only DOCX, atomic no-clobber output và no-finding đã có unit/property/security/contract/UI/performance tests.
- M2: CRUD quy tắc riêng (tiêu đề + prompt + cờ chọn sẵn), `llama-cpp-python` classifier và nhánh full review tùy chọn theo chunk, output có cấu trúc, anchor/coverage fail-closed, timeout/cancel, signed capability gate, nhập gói model, crash recovery, smoke-load/rollback và toggle giải phóng runtime có trong source. Model chỉ `ready` khi package hợp lệ và smoke-load thành công. GGUF nhập cục bộ có thể chạy full review ở chế độ thử nghiệm nhưng vẫn là `local_unverified`; chỉ package ký số kèm benchmark riêng mới được coi là full review đã phê duyệt phát hành.
- M3: Windows CI/NSIS/WebView2 offline config, personal signing và Defender gate đã có. Developer ID/notarization và certificate công khai không nằm trong M0/M1.

Corpus regression tổng hợp có 20 trường hợp và gate precision/recall tự động cho AI filter. Quality gate trên 20 DOCX thật vẫn cần bộ tài liệu ẩn danh và ground truth do người dùng duyệt; đây là evidence đầu vào, không được thay thế bằng dữ liệu giả. Full review phải có corpus/gate riêng đo discovery, coverage, latency và RAM trước khi được coi là capability phát hành.
