# M2 model benchmark

Benchmark là evidence phê duyệt một file GGUF cụ thể, không phải test giả lập. Dùng cùng corpus đã ground-truth trên hai máy/profile RAM 8 GB và 16 GB.

Model có hai capability AI phải đánh giá độc lập:

- **`filter`**: verdict candidate do rule engine sinh;
- **`full review`**: vừa verdict candidate vừa discovery lỗi mới khi đi qua mọi block được hỗ trợ bằng production token-aware chunker.

Đạt gate filter không đồng nghĩa model được phê duyệt cho full review. Manifest schema v2 phân biệt `capabilities.candidate_filter` và `capabilities.full_review`; registry chỉ mở full review cho package `release_signed` có capability này trong nội dung đã ký. Trước khi ký `full_review=true` vẫn phải có benchmark discovery riêng, không chỉ dựa vào trạng thái `ready`.

## Manifest nháp

File này chỉ cấu hình runtime cho benchmark; nó chưa phải signed package manifest:

```json
{
  "model_id": "approved-model",
  "version": "1.0.0",
  "context_size": 2048,
  "batch_size": 8,
  "max_tokens": 512,
  "review_chunk_tokens": 1200,
  "timeout_seconds": 120,
  "seed": 42,
  "minimum_confidence": 0.8
}
```

`review_chunk_tokens` chỉ giới hạn text đích. Khi chạy benchmark, phải ghi nhận cả token của chat template, system prompt và custom rule; output reserve và safety margin không được lấy bớt để làm chunk lớn hơn.

## Corpus cho AI filter

Corpus filter là JSON array. Mỗi case chứa candidates đúng boundary của classifier và danh sách candidate ID phải được giữ:

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

Corpus phải có tối thiểu 20 `document_id` riêng biệt, được trích từ 20 DOCX thật đã ẩn danh. Người duyệt phải chốt `expected_keep` và `ground_truth_total` trước khi chạy model; trường thứ hai gồm cả lỗi tầng candidate bỏ sót để recall là end-to-end, không chỉ recall có điều kiện trên candidates.

## Corpus cho AI full review

Corpus full review biểu diễn toàn bộ **block được hỗ trợ** của từng tài liệu, không phải một danh sách candidate rút gọn. Benchmark phải gọi đúng chunker production để cùng text, tokenizer, context size và safety margin như ứng dụng; không được chuẩn bị sẵn các chunk “đẹp” chỉ cho benchmark.

Ví dụ tối thiểu về dữ liệu logic trước khi chunk:

```json
[
  {
    "document_id": "doc-01",
    "blocks": [
      {
        "paragraph_id": "document:p3",
        "text": "Cơ quan thực hiện sát nhập hai đơn vị."
      },
      {
        "paragraph_id": "document:p4",
        "text": "Tài liệu đã được phê duỵêt."
      }
    ],
    "candidates": [
      {
        "candidate_id": "doc-01-c1",
        "paragraph_id": "document:p3",
        "source_text": "sát nhập",
        "suggestion": "sáp nhập",
        "reason_code": "confusion.sat_nhap.v1",
        "occurrence_index": 0
      }
    ],
    "expected_keep": ["doc-01-c1"],
    "expected_discoveries": [
      {
        "paragraph_id": "document:p4",
        "source_text": "duỵêt",
        "occurrence_index": 0,
        "suggestion": "duyệt",
        "category": "spelling",
        "reason_code": "spelling"
      }
    ],
    "supported_block_total": 2,
    "custom_prompt": ""
  }
]
```

`expected_discoveries` dùng cùng anchor an toàn của production: `paragraph_id + source_text + occurrence_index`, kèm suggestion/category cần chấm. Ground truth phải bao phủ mọi block được hỗ trợ, kể cả block không có lỗi. Header/footer/textbox/footnote chưa hỗ trợ phải được ghi riêng là ngoài scope, không tính vào coverage đã rà.

Không đưa DOCX nhị phân, XML package hoặc toàn bộ text vào **một prompt** ở bất kỳ benchmark nào. “Full review” nghĩa là chunker lần lượt đưa mọi supported block làm target đúng một lần. Context lân cận có thể lặp với role context-only nhưng finding tại đó phải bị production validator loại.

## Gate

### Gate chung

- Cả hai report cùng `model_sha256`, model ID/version và `corpus_sha256`.
- Mỗi report phải chứng minh đã chạy ít nhất 20 document ID riêng biệt.
- Profile RAM do report tự đo phải nằm trong dải 8 GB (7000–9216 MB) và 16 GB (15000–18432 MB).
- P95 và peak RSS phải dương; ngưỡng latency được chốt riêng theo capability và machine profile.
- Packager hash từng report và đưa metadata gate vào payload ký Ed25519.
- Registry chỉ báo `ready` sau khi chữ ký, model bytes, license, quality gate và smoke-load đều hợp lệ.

### Gate AI filter

- Precision ≥ 0,90 và recall end-to-end ≥ 0,85 trên từng profile.
- Báo riêng candidate-generator recall và conditional precision/recall của verdict để không che lỗi rule bỏ sót.
- Batch/token/deadline phải giống cấu hình production cho filter.
- P95 không vượt 180 giây; lệnh benchmark có thể áp ngưỡng chặt hơn cho từng máy.

### Gate AI full review

- Precision ≥ 0,90 và recall ≥ 0,85 trên discovery + candidate cuối cùng của từng profile; không trộn số filter vào để che discovery kém.
- Coverage phải là 100% supported target block/chunk trong success corpus. Mỗi target chỉ được tính một lần dù xuất hiện lại như context.
- Report ghi `total_chunks`, `reviewed_chunks`, `failed_chunks`, phân bố input/output token, p50/p95 theo chunk và theo tài liệu, peak RSS và thời gian tổng.
- Test lỗi riêng phải chứng minh timeout/malformed ở một phần chunk tạo trạng thái `partial`, giữ được finding hợp lệ từ chunk trước và không trả “không phát hiện lỗi”; nếu mọi chunk đều thất bại thì job phải fail-closed. Những case cố ý gây lỗi không được trộn vào success coverage gate.
- Exact-anchor validator phải loại discovery có paragraph lạ, source/occurrence không khớp, finding trong context-only hoặc suggestion sai schema.
- Chunker phải chứng minh không request nào vượt context budget sau khi trừ system/custom prompt, output reservation và safety margin.

Report filter không đạt gate không được dùng để tạo `.svmodel`; model vẫn ở ngoài release và ứng dụng tiếp tục bằng bộ kiểm tra cơ bản. `tools/package_model.py` hiện tạo package filter-only với `full_review=false`. Chỉ quy trình phát hành đã kiểm toán riêng benchmark full review mới được ký manifest `release_signed` có `full_review=true`; trạng thái `ready` một mình không đủ.
