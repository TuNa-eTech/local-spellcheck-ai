# Implementation status

Ngày cập nhật: 24/8/2026

| Milestone | Đã triển khai trong source | Gate cần môi trường/evidence ngoài repo |
|---|---|---|
| M0 | Contracts v1/schema tests, sidecar NDJSON thường trú, progress/error/cancel/frame limits, safe ZIP/XML, advanced golden DOCX, PyInstaller onedir, cleanup khi finalization lỗi, crash propagation và Windows Job Object; [Windows verify #32618189142](https://github.com/TuNa-eTech/local-spellcheck-ai/actions/runs/32618189142) xanh | Word desktop thật cần runner có Office |
| M1 | 4-step UI + DOM tests, word count + saved-page best-effort metadata, prompt counter 1.000 ký tự, 3 preset đúng scope, bật/tắt từng nhóm rule, từ bỏ qua theo phiên, main body/table, technical/confusion/capitalization/syllable-onset rules, NFC property tests, overlap/anchor, annotation-only, atomic no-clobber output, SQLite/CSV Unicode transaction, 50-page benchmark; NSIS được cài/chạy bằng local non-admin user thật, sidecar probe/owner, Defender, no-egress và no-orphan xanh trong verify #32618189142 | Quality corpus thật 20 DOCX chưa được cung cấp |
| M2 | `llama-cpp-python` classifier sau candidate layer; JSON-schema verdict, unknown/malformed fail-closed, global 180-second deadline/cancel, lazy smoke-load và runtime unload khi tắt AI; manifest ký Ed25519 gắn model SHA-256 và quality reports; corpus gate ≥20 document ID đo recall end-to-end; exact HTTPS allowlist, explicit/resumable download, offline import, streaming verify/extract, free-space check, deactivate/swap/rollback và startup crash recovery; benchmark/package tooling và frozen-sidecar capability gate | Chọn GGUF/license được phê duyệt; benchmark cùng corpus thật trên máy 8 GB và 16 GB; endpoint/public key cho connected build; Windows client acceptance |
| M3 | Windows workflow, PyInstaller → Tauri resources, NSIS, WebView2 offline installer, personal self-sign/ad-hoc signing | Certificate công khai, Apple notarization và public distribution không thuộc M0/M1 |

## Quyết định fail-closed hiện tại

- Prompt riêng chỉ được gửi xuống engine khi người dùng bật AI và registry báo `ready`.
- Gói thiếu quality reports 8/16 GB, sai corpus/model hash hoặc không đạt precision/recall bị từ chối. `installed` chỉ còn nghĩa gói đã được duyệt nhưng runtime chưa sẵn sàng; chỉ smoke-load thành công mới là `ready`.
- Không finding thì output tạm bị xoá và không tạo bản sao.
- Span không revalidate hoặc run XML không hỗ trợ sẽ không được annotation.
- Header/footer/textbox/footnote được giữ nguyên trong ZIP nhưng không được kiểm tra ở M1.

Chi tiết từng requirement, lệnh và evidence nằm tại [`m0-m1-acceptance.md`](m0-m1-acceptance.md). GitHub-hosted runner hiện dùng Windows Server, nên không coi run này, Open XML SDK hoặc corpus tổng hợp là bằng chứng thay thế cho Windows 10/11 client + Word 2016/2019/365 và 20 tài liệu thật.
