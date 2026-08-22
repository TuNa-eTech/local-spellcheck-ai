# Implementation status

Ngày cập nhật: 22/8/2026

| Milestone | Đã triển khai trong source | Gate cần môi trường/evidence ngoài repo |
|---|---|---|
| M0 | Contracts v1/schema tests, sidecar NDJSON thường trú, progress/error/cancel/frame limits, safe ZIP/XML, advanced golden DOCX, PyInstaller onedir, crash propagation và Windows Job Object; [Windows verify #32583130994](https://github.com/TuNa-eTech/local-spellcheck-ai/actions/runs/32583130994) xanh | Word desktop thật cần runner có Office |
| M1 | 4-step UI + DOM tests, 3 preset đúng scope, main body/table, technical/confusion/capitalization/syllable-onset rules, NFC property tests, overlap/anchor, annotation-only, atomic no-clobber output, SQLite/CSV Unicode transaction, 50-page benchmark; NSIS current-user/Defender/no-egress/no-orphan xanh trong verify #32583130994 | Quality corpus thật 20 DOCX chưa được cung cấp |
| M2 | Model manifest, exact HTTPS allowlist, explicit download, offline import, size/SHA-256/Ed25519/protocol checks, staging + rollback | llama.cpp classifier, Gemma benchmark, public key/endpoint, quality/RAM/latency gate |
| M3 | Windows workflow, PyInstaller → Tauri resources, NSIS, WebView2 offline installer, personal self-sign/ad-hoc signing | Certificate công khai, Apple notarization và public distribution không thuộc M0/M1 |

## Quyết định fail-closed hiện tại

- Prompt riêng không được gửi xuống engine cho đến khi classifier thật đạt benchmark.
- Gói model hợp lệ được ghi nhận là `installed`, chưa phải `ready`.
- Không finding thì output tạm bị xoá và không tạo bản sao.
- Span không revalidate hoặc run XML không hỗ trợ sẽ không được annotation.
- Header/footer/textbox/footnote được giữ nguyên trong ZIP nhưng không được kiểm tra ở M1.

Chi tiết từng requirement, lệnh và evidence nằm tại [`m0-m1-acceptance.md`](m0-m1-acceptance.md). Không coi Open XML SDK hoặc corpus tổng hợp là bằng chứng thay thế cho Word 2016/2019/365 và 20 tài liệu thật.
