# Implementation status

Ngày cập nhật: 22/8/2026

| Milestone | Đã triển khai trong source | Gate cần môi trường/evidence ngoài repo |
|---|---|---|
| M0 | Contracts v1, sidecar NDJSON thường trú, progress/error/cancel, safe ZIP limits, PyInstaller onedir, Rust process ownership | Windows sạch, Word mở golden corpus không repair, crash/Defender test |
| M1 | 4-step UI, 3 preset, main body/table blocks, technical/confusion/capitalization rules, NFC source map, overlap arbitration, anchor revalidation, highlight + Word comment, no-finding, filename collision, SQLite/CSV transaction | Bộ âm tiết tiếng Việt production, corpus 50 trang và Word 2016/2019/365 acceptance |
| M2 | Model manifest, exact HTTPS allowlist, explicit download, offline import, size/SHA-256/Ed25519/protocol checks, staging + rollback | llama.cpp classifier, Gemma benchmark, public key/endpoint, quality/RAM/latency gate |
| M3 | Windows workflow, PyInstaller → Tauri resources, NSIS, WebView2 offline installer, optional signature verification | Authenticode certificate, signed DLL/EXE verification, standard-user/long-path/Defender lab |

## Quyết định fail-closed hiện tại

- Prompt riêng không được gửi xuống engine cho đến khi classifier thật đạt benchmark.
- Gói model hợp lệ được ghi nhận là `installed`, chưa phải `ready`.
- Không finding thì output tạm bị xoá và không tạo bản sao.
- Span không revalidate hoặc run XML không hỗ trợ sẽ không được annotation.
- Header/footer/textbox/footnote được giữ nguyên trong ZIP nhưng không được kiểm tra ở M1.
