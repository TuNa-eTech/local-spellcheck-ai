# Benchmark examples

Các artifact trong thư mục này chỉ kiểm tra contract và evaluator. Chúng là dữ liệu tổng hợp (`synthetic_example`), không phải bằng chứng chất lượng phát hành và không thay thế corpus DOCX đã ẩn danh, lập ground truth và được con người duyệt.

Chạy lại báo cáo mẫu:

```sh
uv run --project engine python tools/evaluate_findings.py \
  --ground-truth benchmarks/examples/synthetic-ground-truth.json \
  --run benchmarks/examples/synthetic-run.json \
  --output benchmarks/examples/synthetic-report.json
```

Primary metrics chỉ dùng finding khớp chính xác anchor và suggestion được chấp nhận. Finding optional hợp lệ không tăng hoặc giảm precision/recall; `must_not_warn`, duplicate, suggestion sai và finding không khớp đều là FP.
