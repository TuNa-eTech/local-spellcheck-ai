# M0/M1 acceptance — SoátVăn Desktop

Ngày cập nhật: 24/8/2026

Tài liệu này phân biệt rõ **implementation**, **test tự động**, **evidence Windows GitHub Actions** và **evidence đầu vào chưa có**. Không dùng kết quả macOS để tuyên bố một gate Windows đã đạt.

Evidence Windows hiện tại: commit `ad8ae1a`, [verify #32618189142](https://github.com/TuNa-eTech/local-spellcheck-ai/actions/runs/32618189142) xanh cho cả `engine` và `desktop`. Run tạo PyInstaller onedir, kiểm tra frozen sidecar khi `PATH` không có Python, validate DOCX bằng Open XML SDK, build NSIS kèm WebView2 offline và tạo tài khoản local tạm không thuộc `Administrators`. Installer, sidecar probe và app đều chạy bằng credential của tài khoản đó với user environment riêng; manifest `asInvoker`, owner process tree, zero egress, no orphan và Microsoft Defender scan đều đạt. Runner cụ thể là Windows Server hosted runner; đây không phải bằng chứng Windows 10/11 client hay Microsoft Word desktop.

## M0

| Requirement | Evidence tự động | Trạng thái |
|---|---|---|
| Ba vùng `apps/desktop`, `engine`, `contracts` | Architecture test và repository layout | Đã triển khai |
| Protocol v1, handshake, progress, cancel, structured error và frame limit | `test_contracts.py`, `test_sidecar.py` | Đã triển khai |
| Sidecar thường trú, stdout chỉ có NDJSON | Subprocess contract tests và frozen-sidecar acceptance | Đã triển khai |
| Round-trip OOXML có split formatting, bảng, ảnh, hyperlink, header/footer, comment cũ, tracked change, content control | `test_golden_package_preserves_unsupported_parts_and_existing_annotations`; ZIP inventory byte-identical ngoài mutation scope | Đã triển khai |
| DOCX không repair | LibreOffice render local; Open XML SDK 3.5.1 chạy trên Windows Actions | Structural gate đạt; Word desktop thật là gate riêng |
| PyInstaller `onedir`, không cần Python cài sẵn | `windows_frozen_acceptance.py` chạy `.exe` với `PATH` chỉ còn `System32` | Đạt trên Windows Actions |
| Cancel/crash không đổi nguồn, không để output tạm hoặc process mồ côi | Python export/cancel tests; Rust EOF, finalize-failure cleanup và Windows Job Object tests; installed-app kiểm tra toàn bộ process tree | Đạt trên Windows Actions |

## M1

| Requirement | Evidence tự động | Trạng thái |
|---|---|---|
| Workflow `Chọn file → Quy tắc → Xử lý → Kết quả`, không preview | 12 Vitest/jsdom tests | Đã triển khai |
| File dialog, Tauri drop, `Ctrl+O`, progress, cancel, output/open-folder | DOM tests; late-result-after-cancel regression | Đã triển khai |
| Ba preset đúng scope | Rule tests chứng minh preset spelling không chạy technical rules | Đã triển khai |
| Bật/tắt từng nhóm rule và từ bỏ qua chỉ trong phiên hiện tại | DOM/API/Rust/Python boundary tests và workflow tests | Đã triển khai |
| Prompt tự do khóa khi model chưa `ready` | DOM test và Rust fail-closed command | Đã triển khai |
| NFC, khoảng trắng/dấu câu/từ lặp, confusion, viết hoa hành chính, âm tiết | Unit + Hypothesis property tests; detector âm tiết M1 chỉ sửa vi phạm phụ âm đầu có độ tin cậy cao | Đã triển khai theo scope bảo thủ |
| Main body/table; giữ nguyên vùng chưa hỗ trợ | Advanced golden inventory và semantic assertions | Đã triển khai |
| Annotation-only, vàng + comment, không overlap | DOCX/rule/property tests; source hash bất biến | Đã triển khai |
| `name-soat.docx`, collision suffix, atomic no-clobber; no finding không output | Rust hard-link finalization test và Python workflow tests | Đã triển khai |
| SQLite dictionary CRUD/search và CSV UTF-8 BOM transaction | Unicode casefold CRUD/search/export/import/rollback tests | Đã triển khai |
| ZIP/XML security | traversal, backslash/drive path, duplicate entry, compression bomb, malformed XML, external entity, external relationship và size-limit tests | Đã triển khai |
| Contract/crash/timeout/cancel | Schema parity, ordering, frame limit, cancel, EOF propagation; timeout chủ động gửi cancel | Đã triển khai |
| Rule layer ≤2 giây trên corpus chuẩn hóa 50 trang | 50 × 500 âm tiết, assertion 2 giây; Actions ghi thời gian test | Đạt trên Windows Actions |
| Precision ≥90%, recall ≥85% | 20 case regression tổng hợp đạt gate | Đạt regression; chưa phải customer evidence |
| Windows sạch/offline/standard user/Defender/long path/no orphan/no egress | Frozen acceptance kiểm tra Unicode/space/path >270; installed-package acceptance tạo non-admin user thật, probe sidecar, kiểm tra owner/process tree/network và Defender | Đạt trên Windows Actions hosted runner |

## Hai evidence không được giả lập

1. **20 DOCX thật có ground truth đã duyệt:** repository hiện không có dữ liệu này. Corpus 20 case tổng hợp chỉ là regression test và không được ghi nhận như customer quality gate.
2. **Word 2016/2019/365 không hiện repair prompt:** runner `windows-latest` không cung cấp ba bản Microsoft Word. Open XML SDK và LibreOffice là structural/openability gates mạnh nhưng không phải bằng chứng thay Word desktop.

Windows Actions hiện tại đã xanh. Source và toàn bộ gate tự động khả dụng trên GitHub-hosted Windows đã hoàn tất. M0/M1 chỉ được đánh dấu hoàn tất tuyệt đối sau khi hai evidence trên được cung cấp hoặc người dùng chính thức thay đổi acceptance.
