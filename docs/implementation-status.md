# Implementation status

Ngày cập nhật: 25/8/2026

| Milestone | Đã triển khai trong source | Gate cần môi trường/evidence ngoài repo |
|---|---|---|
| M0 | Contracts v1/schema tests, sidecar NDJSON thường trú, progress/error/cancel/frame limits, safe ZIP/XML, advanced golden DOCX, PyInstaller onedir, cleanup khi finalization lỗi, crash propagation và Windows Job Object; [Windows verify #32618189142](https://github.com/TuNa-eTech/local-spellcheck-ai/actions/runs/32618189142) xanh | Word desktop thật cần runner có Office |
| M1 | UI bốn bước + DOM tests, word count + saved-page best-effort metadata, bộ kiểm tra cơ bản cố định không có preset/toggle/từ bỏ qua, main body/table, technical/confusion/capitalization/syllable-onset rules, NFC property tests, overlap/anchor, annotation-only, atomic no-clobber output và benchmark 50 trang; NSIS được cài/chạy bằng local non-admin user thật, sidecar probe/owner, Defender, no-egress và no-orphan xanh trong verify #32618189142 | Quality corpus thật 20 DOCX chưa được cung cấp |
| M2 | CRUD quy tắc riêng trong SQLite (`prompt` text, tổng tối đa 4.000 ký tự), ghép các mục thành context chung khi AI bật; `llama-cpp-python` classifier sau candidate layer; ba mode suy ra từ `use_model/full_review`; full review dùng token-aware chunks, constrained discovery, exact-anchor validation và review coverage; JSON output fail-closed, timeout/cancel, lazy smoke-load và runtime unload; manifest ký Ed25519 gắn model SHA-256, quality reports và capability phê duyệt; exact HTTPS allowlist, import/download/rollback và benchmark/package tooling | Chọn GGUF/license được phê duyệt; benchmark filter và full-review cùng corpus thật trên máy 8 GB và 16 GB; endpoint/public key cho connected build; Windows client acceptance |
| M3 | Windows workflow, PyInstaller → Tauri resources, NSIS, WebView2 offline installer, personal self-sign/ad-hoc signing | Certificate công khai, Apple notarization và public distribution không thuộc M0/M1 |

## Quyết định fail-closed hiện tại

- Bộ kiểm tra cơ bản có cấu hình cố định và luôn chạy. Dữ liệu từ điển SQLite cũ không còn được public qua IPC và không còn suppress finding; các field `preset`, `rule_config`, `ignored_words` chỉ còn để tương thích protocol nội bộ, UI luôn gửi mặc định cố định/rỗng.
- Mỗi quy tắc riêng là một prompt text CRUD độc lập. UI ghép tất cả mục theo thứ tự bằng `\n\n`; text lưu tối đa 4.000 ký tự và transport tối đa 4.200 ký tự. Context này chỉ được gửi khi người dùng bật AI và registry báo `ready`.
- `use_model=false` luôn là kiểm tra cơ bản; `use_model=true, full_review=false` là AI filter; `use_model=true, full_review=true` là AI full review. Full review không chạy khi model tắt hoặc package không có capability `full_review` đã ký.
- Gói sai corpus/model hash, thiếu quality evidence hoặc không đạt gate bị từ chối. GGUF nhập trực tiếp được đánh dấu `local_unverified`, chỉ dùng đánh giá filter và không được full review. `installed` nghĩa package nằm trên máy nhưng runtime chưa hoạt động; chỉ smoke-load thành công mới là `ready`.
- Không finding thì output tạm bị xoá và không tạo bản sao.
- Finding nằm hoàn toàn trong run thường vẫn được annotation dù cùng paragraph có hyperlink/tracked change/content control. Finding không thể neo an toàn không được ghi; nếu không finding nào xuất được thì job báo `DOCUMENT_FINDINGS_NOT_EXPORTABLE`, còn full review phải phản ánh block đó bằng coverage `partial`.
- Discovery full review ngoài target chunk, trỏ vào context-only hoặc không khớp chính xác `paragraph_id + source_text + occurrence_index` bị loại.
- Full review có một phần chunk timeout/malformed trả coverage `partial`; nếu mọi chunk đều thất bại thì job báo `MODEL_FULL_REVIEW_FAILED`. UI không được diễn giải hai trường hợp này thành “không phát hiện cảnh báo”.
- Header/footer/textbox/footnote được giữ nguyên trong ZIP nhưng không được kiểm tra ở M1.

Chi tiết từng requirement, lệnh và evidence nằm tại [`m0-m1-acceptance.md`](m0-m1-acceptance.md). GitHub-hosted runner hiện dùng Windows Server, nên không coi run này, Open XML SDK hoặc corpus tổng hợp là bằng chứng thay thế cho Windows 10/11 client + Word 2016/2019/365 và 20 tài liệu thật.
