# Prompt tham khảo cho AI full review

> **Trạng thái:** Prompt này là tài liệu tham khảo cho một lượt rà soát LLM chuyên biệt hoặc cho lần benchmark tiếp theo. Với `gemma-4-e2b` chạy đơn lượt hiện tại, benchmark trên 71 annotations đã duyệt vẫn cho kết quả tốt nhất khi không có custom prompt (`empty-baseline`). Không nên coi prompt dưới đây là cấu hình phát hành trước khi đo lại.

## Prompt

```text
Rà soát khách quan từng segment có role=target; dùng context chỉ để hiểu và đối chiếu. Quét toàn bộ target từ trái sang phải, không dừng sau lỗi đầu.

Kiểm tra mọi từ để tìm lỗi gõ, phụ âm hoặc vần sai, thiếu hay nhầm dấu tiếng Việt, từ đúng chính tả nhưng sai trong cụm đang dùng, từ bị lặp liền nhau, và khoảng trắng rõ ràng bị thừa hoặc thiếu. Khi target có ngày hoặc số liệu, kiểm tra ngày có tồn tại, thứ tự thời gian có hợp lý, và tự tính lại tổng, hiệu, số lượng, tiền hoặc tỷ lệ từ các số thành phần nhìn thấy. Chỉ báo mâu thuẫn khi bằng chứng trong target cho thấy hai điều không thể đồng thời đúng.

Chỉ báo lỗi có thể chứng minh trực tiếp từ văn bản. Không chuẩn hóa phong cách, tiêu đề viết hoa, số 0 đứng đầu hợp lệ, dấu kết thúc câu hợp lệ, chữ viết tắt, mã hiệu, URL hoặc phần mở rộng tệp. Không bịa dữ kiện còn thiếu.

Mỗi lỗi là một discovery riêng. source_text phải sao chép nguyên văn phần sai ngắn nhất trong đúng target; suggestion chỉ là một thay thế cục bộ. Không viết lại cả câu hoặc đoạn, không thêm trường, không đổi JSON schema, không tự tạo segment_id và phải tính occurrence_index trong đúng segment.
```

## Cách sử dụng

- Dùng như `custom_prompt` bổ sung; system prompt của engine vẫn quyết định schema output.
- Giữ nguyên model, DOCX, chunk budget và cấu hình khi so sánh với baseline.
- Chỉ chấp nhận prompt nếu report mới tăng recall/F1 mà precision vẫn đạt ngưỡng đã chọn.
- Không đưa ground truth, từ sai cụ thể hoặc đáp án của tài liệu benchmark vào prompt.
