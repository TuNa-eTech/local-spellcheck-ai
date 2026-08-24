# M2 model benchmark

Benchmark là evidence phê duyệt một file GGUF cụ thể, không phải test giả lập. Dùng cùng corpus đã ground-truth trên hai máy/profile RAM 8 GB và 16 GB.

## Manifest nháp

File này chỉ cấu hình runtime cho benchmark; nó chưa phải signed package manifest:

```json
{
  "model_id": "approved-model",
  "version": "1.0.0",
  "context_size": 2048,
  "batch_size": 8,
  "max_tokens": 512,
  "timeout_seconds": 120,
  "seed": 42,
  "minimum_confidence": 0.8
}
```

## Corpus

Corpus là JSON array. Mỗi case chứa candidates đúng boundary của classifier và danh sách candidate ID phải được giữ:

```json
[
  {
    "document_id": "doc-01",
    "candidates": [
      {
        "candidate_id": "doc-01-c1",
        "paragraph_id": "document:p3",
        "source_text": "sát nhập",
        "suggestion": "sáp nhập",
        "reason_code": "confusion.sat_nhap.v1",
        "occurrence_index": 0,
        "context": "Cơ quan thực hiện sát nhập hai đơn vị."
      }
    ],
    "expected_keep": ["doc-01-c1"],
    "ground_truth_total": 1,
    "custom_prompt": ""
  }
]
```

Không đưa toàn bộ DOCX vào prompt benchmark. Corpus phải có tối thiểu 20 `document_id` riêng biệt, được trích từ 20 DOCX thật đã ẩn danh. Người duyệt phải chốt `expected_keep` và `ground_truth_total` trước khi chạy model; trường thứ hai gồm cả lỗi tầng candidate bỏ sót để recall là end-to-end, không chỉ recall có điều kiện trên candidates.

## Gate

- Cả hai report cùng `model_sha256`, model ID/version và `corpus_sha256`.
- Mỗi report phải chứng minh đã chạy ít nhất 20 document ID riêng biệt.
- Precision ≥ 0,90 và recall ≥ 0,85 trên từng profile.
- Profile RAM do report tự đo phải nằm trong dải 8 GB (7000–9216 MB) và 16 GB (15000–18432 MB).
- P95 phải dương và không vượt 180 giây; peak RSS phải dương. Lệnh benchmark có thể áp ngưỡng chặt hơn cho từng máy.
- Packager hash từng report và đưa metadata gate vào payload ký Ed25519.
- Registry chỉ báo `ready` sau khi chữ ký, model bytes, license, quality gate và smoke-load đều hợp lệ.

Report không đạt gate không được dùng để tạo `.svmodel`; model vẫn ở ngoài release và ứng dụng tiếp tục chạy rule-only.
