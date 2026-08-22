# PRD — Công cụ soát lỗi văn bản tiếng Việt (SoátVăn)

| | |
|---|---|
| **Phiên bản** | 0.1 — bản nháp nội bộ |
| **Ngày** | 22/8/2026 |
| **Trạng thái** | Chờ chốt phạm vi với khách |
| **Loại sản phẩm** | Ứng dụng desktop Windows, chạy hoàn toàn offline |

---

## 1. Tóm tắt

Xây dựng phần mềm desktop nhận file Word, phát hiện lỗi chính tả và trình bày tiếng Việt, trả về file Word có bôi vàng và comment kèm gợi ý sửa. Toàn bộ xử lý chạy trên máy người dùng, không có kết nối mạng.

Kiến trúc lai: **tầng từ điển và luật** xử lý phần lớn lỗi trong dưới 1 giây với độ chính xác gần tuyệt đối, **tầng LLM offline** chỉ bổ sung cho lỗi ngữ cảnh và có thể tắt.

---

## 2. Bối cảnh và vấn đề

Đơn vị khách hàng soát lỗi văn bản thủ công. Word không hỗ trợ chính tả tiếng Việt ở mức dùng được — không phân biệt được *sáp nhập / sát nhập*, *chẩn đoán / chuẩn đoán*. Các công cụ AI trực tuyến giải được bài toán nhưng vi phạm yêu cầu bảo mật, vì tài liệu phải rời khỏi mạng nội bộ.

**Chi phí của việc không giải quyết:** lỗi lọt ra văn bản chính thức, và thời gian cán bộ dành cho việc soát tay lặp đi lặp lại.

---

## 3. Mục tiêu

| # | Mục tiêu | Cách đo |
|---|---|---|
| G1 | Phát hiện được phần lớn lỗi mà người soát tay tìm ra | Recall ≥ 85% trên bộ 20 file mẫu đã chốt |
| G2 | Người dùng tin tưởng kết quả, không phải kiểm tra lại từng cảnh báo | Precision ≥ 90% (báo nhầm ≤ 10%) |
| G3 | Không làm chậm quy trình hiện tại | Tầng luật ≤ 2 giây cho tài liệu 50 trang |
| G4 | Dữ liệu không rời khỏi máy | Xác minh bằng kiểm tra lưu lượng mạng khi chạy |
| G5 | Người dùng duyệt hết lỗi trong một tài liệu mà không bỏ dở | ≥ 80% phiên làm việc kết thúc bằng thao tác xuất file |

---

## 4. Không phải mục tiêu (v1)

| Không làm | Lý do |
|---|---|
| Kiểm tra ngữ pháp và văn phong | Khách xác nhận chưa cần. Độ chính xác của LLM offline ở mảng này chưa đủ tin cậy |
| Sửa nội dung trực tiếp trong preview | Biến sản phẩm thành trình soạn thảo — bẫy phạm vi lớn nhất |
| Xử lý tài liệu dùng phông chữ đời cũ (.VnTime, VNI) | Chưa biết tỷ trọng. Tách thành hạng mục tính phí riêng |
| Xử lý hàng loạt nhiều file | Chưa có nhu cầu xác nhận từ khách |
| Bản macOS / Linux | Tăng ~30% công test, khách dùng Windows |
| Đồng bộ từ điển giữa nhiều máy | Cần hạ tầng server, mâu thuẫn với yêu cầu offline |

---

## 5. Người dùng

**Người dùng chính — cán bộ văn thư / soạn thảo văn bản.** Không rành kỹ thuật. Soát 1–5 tài liệu mỗi tuần, mỗi tài liệu 20–100 trang. Quan tâm: nhanh, không phải học, không tự ý sửa tài liệu của họ.

**Người dùng phụ — quản trị viên / đầu mối kỹ thuật của đơn vị.** Cài đặt phần mềm, duy trì từ điển nội bộ và các mẫu hướng dẫn.

### User stories

**Cán bộ văn thư**

- Là cán bộ văn thư, tôi muốn kéo file Word vào phần mềm và thấy ngay danh sách lỗi, để không phải đọc lại toàn bộ tài liệu.
- Là cán bộ văn thư, tôi muốn mỗi lỗi nêu rõ lý do, để tự quyết định có sửa hay không thay vì tin mù quáng.
- Là cán bộ văn thư, tôi muốn duyệt lỗi bằng bàn phím, để xử lý 200 lỗi mà không mỏi tay.
- Là cán bộ văn thư, tôi muốn hoàn tác thao tác vừa rồi, để không sợ bấm nhầm.
- Là cán bộ văn thư, tôi muốn nhận lại file Word bôi vàng kèm comment, để gửi cho lãnh đạo xem trên Word như bình thường.
- Là cán bộ văn thư, tôi muốn phần mềm ngừng báo sai tên đơn vị của tôi, để danh sách lỗi không đầy cảnh báo rác.
- Là cán bộ văn thư, tôi muốn dặn phần mềm vài quy tắc riêng trước khi soát, để nó bám theo quy ước của cơ quan.
- Là cán bộ văn thư soạn tài liệu dài, tôi muốn biết còn bao lâu nữa xong, để quyết định chờ hay làm việc khác.

**Quản trị viên**

- Là quản trị viên, tôi muốn quản lý danh sách từ riêng của đơn vị tập trung một chỗ, để mọi người dùng chung một chuẩn.
- Là quản trị viên, tôi muốn tắt được phần AI, để phần mềm vẫn chạy trên máy cấu hình thấp.
- Là quản trị viên, tôi muốn cài phần mềm không cần internet, để triển khai được trong mạng nội bộ tách biệt.

---

## 6. Yêu cầu chức năng

### P0 — Bắt buộc để phát hành

**F1. Mở tài liệu**
- Kéo-thả hoặc chọn từ hộp thoại, định dạng `.docx`
- ✅ Given file .docx hợp lệ, when người dùng thả vào cửa sổ, then phần mềm hiển thị tên file, số trang, số từ
- ✅ Given file không phải .docx, when thả vào, then hiện thông báo nêu rõ định dạng được hỗ trợ, không crash

**F2. Nhập hướng dẫn trước khi soát**
- Ô danh sách từ bỏ qua (nạp vào tầng từ điển)
- Ô quy tắc tự do, giới hạn 1.000 ký tự (nạp vào prompt LLM)
- ✅ Given người dùng thêm từ vào danh sách bỏ qua, when soát, then từ đó không xuất hiện trong bất kỳ cảnh báo nào
- ✅ Given ô quy tắc vượt 1.000 ký tự, when nhập tiếp, then chặn nhập và hiện bộ đếm

**F3. Soát lỗi — tầng luật**
- Chuẩn hoá Unicode NFC trước mọi xử lý
- Lỗi kỹ thuật: khoảng trắng thừa/thiếu, dấu câu sai vị trí, lặp từ
- Âm tiết không tồn tại trong tiếng Việt
- Từ ghép dễ nhầm (danh sách confusion set)
- Quy tắc viết hoa theo Nghị định 30/2020, Phụ lục II
- ✅ Given tài liệu 50 trang, when chạy tầng luật, then hoàn tất trong ≤ 2 giây
- ✅ Given tài liệu chứa `ngiên cứu`, when soát, then báo lỗi và đề xuất `nghiên cứu`
- ✅ Given phần mềm chạy trên máy không có GPU, when soát bằng tầng luật, then vẫn chạy đầy đủ

**F4. Soát lỗi — tầng ngữ cảnh (LLM)**
- Chạy sau tầng luật, chỉ với các trường hợp còn nghi ngờ
- Bật/tắt được từ Cài đặt
- ✅ Given tắt tầng LLM, when soát, then phần mềm chạy bình thường, chỉ thiếu nhóm lỗi ngữ cảnh
- ✅ Given LLM trả về đề xuất không khớp với văn bản gốc, when xử lý kết quả, then bỏ qua đề xuất đó, không hiển thị

**F5. Preview tài liệu và bôi vàng lỗi**
- Hiển thị nội dung giữ cấu trúc cơ bản: đoạn, tiêu đề, bảng, in đậm/nghiêng
- Mỗi lỗi bôi vàng, bấm vào nhảy tới thẻ lỗi tương ứng
- ✅ Given tài liệu có bảng, when preview, then bảng hiển thị đúng cấu trúc hàng cột

**F6. Danh sách lỗi**
- Mỗi thẻ: loại lỗi, vị trí, sai → đúng, lý do
- Phân loại theo nguồn: chính tả / từ ghép / viết hoa / kỹ thuật / quy tắc riêng / LLM
- Lọc theo loại, có số đếm
- ✅ Given có lỗi viết hoa, when xem thẻ, then phần lý do trích dẫn điều khoản Nghị định 30
- ✅ Given lỗi do LLM phát hiện, when xem thẻ, then nhãn phân biệt rõ với lỗi từ tầng luật

**F7. Duyệt lỗi**
- Nút Sửa / Bỏ qua trên từng thẻ
- **Hoàn tác** thao tác vừa thực hiện
- **Phím tắt:** Enter = sửa, Esc = bỏ qua, ↑/↓ = chuyển lỗi
- ✅ Given vừa bấm Sửa, when nhấn Ctrl+Z, then thao tác được hoàn tác, chữ trong preview trở về trạng thái cũ
- ✅ Given đang chọn một lỗi, when nhấn Enter, then áp dụng sửa và tự chuyển sang lỗi kế tiếp

**F8. Xuất file Word**
- Tạo **file mới**, không ghi đè bản gốc
- Chỗ sai bôi vàng, kèm comment chứa từ đúng và lý do
- Giữ nguyên toàn bộ định dạng, ảnh, bảng, header/footer của bản gốc
- ✅ Given file đã xuất, when mở bằng Microsoft Word, then không có cảnh báo lỗi file
- ✅ Given tài liệu gốc có comment sẵn, when xuất, then comment cũ được giữ nguyên
- ✅ Given người dùng bỏ qua một lỗi, when xuất, then lỗi đó không xuất hiện trong file

**F9. Quản lý từ điển nội bộ**
- Màn hình riêng: thêm, sửa, xoá, tìm kiếm
- Nhập/xuất CSV
- ✅ Given từ có trong từ điển nội bộ, when soát bất kỳ tài liệu nào, then không báo lỗi từ đó

**F10. Cài đặt cơ bản**
- Bật/tắt từng nhóm quy tắc
- Bật/tắt tầng LLM
- ✅ Given tắt nhóm quy tắc viết hoa, when soát, then không có lỗi nào thuộc nhóm đó

**F11. Cài đặt phần mềm offline**
- Bộ cài chạy được trên máy không có internet
- ✅ Given máy hoàn toàn không có mạng, when cài đặt và chạy, then phần mềm hoạt động đầy đủ

### P1 — Nên có

- F12. Mẫu hướng dẫn lưu sẵn theo loại văn bản
- F13. Danh sách tài liệu mở gần đây
- F14. Ngưỡng tin cậy cho tầng LLM
- F15. Tuỳ chọn kiểu bỏ dấu (`hòa` / `hoà`) — mặc định chỉ cảnh báo khi không nhất quán
- F16. Điểm tin cậy hiển thị trên thẻ lỗi do LLM phát hiện
- F17. Áp dụng tất cả — chỉ cho nhóm lỗi từ tầng luật
- F18. Sửa hướng dẫn rồi soát lại không cần mở file lại
- F19. Thanh tiến độ theo từng tầng, có ước tính thời gian
- F20. Xuất báo cáo lỗi ra Excel
- F21. Tự động đề xuất thêm vào từ điển khi một từ lạ lặp lại ≥ 3 lần
- F22. Cột lề biên tập với ký hiệu theo dòng

### P2 — Tính đến khi thiết kế, không làm ở v1

- Xuất dạng track changes để Accept/Reject trong Word
- Xử lý hàng loạt
- Đọc `.doc` (Word 97-2003)
- Nhận diện và chuyển bảng mã phông chữ cũ
- Kiểm tra ngữ pháp và văn phong
- Nhật ký thao tác phục vụ kiểm toán

---

## 7. Yêu cầu phi chức năng

| | Yêu cầu |
|---|---|
| **Hiệu năng** | Tầng luật ≤ 2s/50 trang. Tầng LLM ≤ 3 phút/50 trang trên cấu hình khuyến nghị |
| **Bảo mật** | Không phát sinh bất kỳ kết nối mạng nào khi chạy. Kiểm chứng được bằng công cụ giám sát lưu lượng |
| **Toàn vẹn dữ liệu** | Không bao giờ ghi đè file gốc. Mọi kết quả ghi ra file mới |
| **Cấu hình tối thiểu** | Windows 10, 8 GB RAM, 15 GB trống — *cần xác nhận sau PoC* |
| **Khả năng chịu lỗi** | File hỏng hoặc quá lớn phải báo lỗi rõ ràng, không crash |
| **Giấy phép** | Mọi thành phần bên thứ ba phải cho phép dùng thương mại và phân phối lại |

---

## 8. Kiến trúc tóm tắt

```
.docx
  └→ đọc XML, trích text theo paragraph (giữ map paragraph → run)
  └→ chuẩn hoá Unicode NFC
  └→ Tầng 0: regex lỗi kỹ thuật
  └→ Tầng 1: từ điển âm tiết
  └→ Tầng 2: confusion set từ ghép
  └→ Tầng 3: rule engine viết hoa (NĐ 30)
  └→ Tầng 4: LLM chấm điểm các trường hợp nghi ngờ  ← tắt được
  └→ neo lỗi về paragraph bằng khớp chuỗi (KHÔNG dùng offset do LLM trả về)
  └→ tách run, chèn highlight + comment
  └→ đóng gói .docx mới
```

**Hai quyết định kỹ thuật bắt buộc tuân thủ:**

1. **Không tin offset do LLM trả về.** LLM trả `{paragraph_id, chuỗi_sai, occurrence_index, đề_xuất, lý_do}`. Code tự tìm chuỗi trong đúng paragraph. Không khớp thì loại bỏ, không đoán.
2. **LLM chỉ phân loại, không sinh tự do.** Tầng luật sinh ứng viên, LLM trả lời đúng/sai kèm điểm tin cậy. Model nhỏ làm việc này ổn định hơn hẳn so với để nó tự đề xuất.

---

## 9. Chỉ tiêu nghiệm thu

Đo trên bộ **20 tài liệu thật** do khách cung cấp và **chốt trước khi bắt đầu code**, đã được cán bộ đơn vị soát tay và đánh dấu lỗi.

| Chỉ tiêu | Ngưỡng đạt |
|---|---|
| Recall — tỷ lệ bắt đúng lỗi đã đánh dấu | ≥ 85% |
| Precision — tỷ lệ cảnh báo đúng | ≥ 90% |
| Tính toàn vẹn file xuất | 20/20 file mở được bằng Word, định dạng không đổi |
| Thời gian tầng luật | ≤ 2 giây cho tài liệu 50 trang |

> ⚠️ **Điều khoản cần đưa vào hợp đồng:** chỉ tiêu trên đo ở **cấu hình mặc định**, không bao gồm các quy tắc do người dùng tự nhập ở mục F2. Quy tắc tự do là công cụ hỗ trợ, không nằm trong phạm vi cam kết chất lượng.

---

## 10. Rủi ro

| Rủi ro | Mức | Cách giảm |
|---|---|---|
| LLM offline không đủ tốt với tiếng Việt | **Cao** | PoC 2 tuần đo trước. Kiến trúc để tầng luật đứng độc lập, tắt LLM vẫn còn sản phẩm |
| Preview docx khó làm giống Word | Cao | Chấp nhận giữ cấu trúc cơ bản, không cam kết giống 100%. Chốt rõ với khách |
| Tách run làm hỏng định dạng | Trung bình | Bộ test hồi quy trên 20 file, so sánh XML trước/sau |
| Tỷ lệ tài liệu dùng phông chữ cũ cao hơn dự kiến | Trung bình | Đã đưa vào P2 và ghi rõ là hạng mục tính phí riêng |
| Phạm vi phình theo yêu cầu phát sinh | Trung bình | Danh sách Không-phải-mục-tiêu ở mục 4. Mọi bổ sung đi kèm change order |
| Máy người dùng quá yếu cho LLM | Thấp | Tắt được tầng LLM, sản phẩm vẫn dùng tốt |

---

## 11. Câu hỏi mở

**Chặn — cần trả lời trước khi bắt đầu**

| Câu hỏi | Hỏi ai |
|---|---|
| "Lỗi kỹ thuật" theo định nghĩa của khách gồm những gì? | Khách |
| Hạ tầng có GPU không? VRAM bao nhiêu? | Khách / IT |
| Bao nhiêu % tài liệu dùng phông chữ đời cũ? | Khách |
| Có soát cả nội dung trong bảng, header/footer, textbox không? | Khách |
| Quy tắc viết hoa theo NĐ 30 hay quy định riêng của đơn vị? | Khách |
| Cơ chế bản quyền và giới hạn số máy cài? | Nội bộ |

**Không chặn — giải quyết trong quá trình làm**

| Câu hỏi | Hỏi ai |
|---|---|
| Danh sách từ bỏ qua gắn theo mẫu hay theo từ điển vĩnh viễn? | Thiết kế |
| Cập nhật phần mềm và từ điển cho máy offline bằng cách nào? | Kỹ thuật |
| Có cần nhật ký thao tác phục vụ kiểm toán không? | Khách |
| Ai chịu trách nhiệm duy trì từ điển nội bộ phía khách? | Khách |

---

## 12. Lộ trình

| Giai đoạn | Nội dung | Thời gian |
|---|---|---|
| **PoC** | Đo Gemma 4 trên 20 file thật. Demo luồng docx → highlight + comment. Báo cáo số liệu và báo giá chính xác | 2 tuần |
| **Phase 1** | Toàn bộ P0, không có tầng LLM. Sản phẩm hoàn chỉnh dùng được | 4 tuần |
| **Phase 2** | Tầng LLM, các mục P1 | 2–3 tuần |
| **Phase 3** | Đào tạo, bàn giao, bảo hành | 1 tuần |

**Nguyên tắc phân kỳ:** kết thúc Phase 1 phải có một sản phẩm giao được, dùng được, không phụ thuộc vào việc tầng LLM có thành công hay không. Đây là biện pháp đẩy rủi ro lớn nhất ra khỏi đường găng.