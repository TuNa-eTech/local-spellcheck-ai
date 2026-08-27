# PRD — Công cụ soát lỗi văn bản tiếng Việt (SoátVăn)

| | |
|---|---|
| **Phiên bản** | 0.3 — bộ kiểm tra cố định + CRUD quy tắc riêng |
| **Ngày** | 25/8/2026 |
| **Trạng thái** | Phạm vi sản phẩm hiện hành |
| **Loại sản phẩm** | Ứng dụng desktop Windows, xử lý tài liệu hoàn toàn cục bộ |

---

## 1. Tóm tắt

Xây dựng phần mềm desktop nhận file Word, phát hiện lỗi chính tả và trình bày tiếng Việt, trả về file Word có bôi vàng và comment kèm gợi ý sửa. Toàn bộ nội dung tài liệu được xử lý trên máy người dùng. Build air-gap không có mạng; build connected chỉ kết nối khi người dùng chủ động tải model từ endpoint allowlist và không gửi nội dung tài liệu.

Kiến trúc lai: **bộ kiểm tra cơ bản cố định** luôn chạy để phát hiện lỗi kỹ thuật và các lỗi có độ tin cậy cao; **LLM offline** có thể tắt, chỉ lọc candidate hoặc rà toàn bộ phần nội dung được hỗ trợ theo các chunk giới hạn token để bổ sung lỗi ngữ cảnh. Người dùng không cấu hình preset/nhóm rule/từ điển; phần tuỳ biến duy nhất là CRUD các **quy tắc riêng**, mỗi mục là một đoạn prompt text được ghép thành context chung khi AI bật.

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
| G4 | Dữ liệu tài liệu không rời khỏi máy | Zero-egress trên đường xử lý; provisioning model được kiểm tra riêng và không có request nền |
| G5 | Người dùng hoàn thành luồng mà không bỏ dở | ≥ 80% phiên làm việc kết thúc bằng việc tạo/mở file kết quả |

---

## 4. Không phải mục tiêu (v1)

| Không làm | Lý do |
|---|---|
| Tự động viết lại/cải biên văn phong | Khó kiểm soát ý nghĩa và vượt quá workflow cảnh báo + comment. Full review chỉ trả span lỗi ngữ pháp/chọn từ có anchor và gợi ý ngắn |
| Sửa nội dung trực tiếp trong preview | Biến sản phẩm thành trình soạn thảo — bẫy phạm vi lớn nhất |
| Xử lý tài liệu dùng phông chữ đời cũ (.VnTime, VNI) | Chưa biết tỷ trọng. Tách thành hạng mục tính phí riêng |
| Xử lý hàng loạt nhiều file | Chưa có nhu cầu xác nhận từ khách |
| Bản macOS / Linux | Tăng ~30% công test, khách dùng Windows |
| Từ điển người dùng, preset hoặc bật/tắt từng nhóm rule | Tăng cấu hình và tạo nhiều trạng thái chất lượng khó kiểm soát; bộ kiểm tra cơ bản được cố định |

---

## 5. Người dùng

**Người dùng chính — cán bộ văn thư / soạn thảo văn bản.** Không rành kỹ thuật. Soát 1–5 tài liệu mỗi tuần, mỗi tài liệu 20–100 trang. Quan tâm: nhanh, không phải học, không tự ý sửa tài liệu của họ.

**Người dùng phụ — quản trị viên / đầu mối kỹ thuật của đơn vị.** Cài đặt phần mềm, quản lý model offline và hỗ trợ xây dựng các quy tắc riêng.

### User stories

**Cán bộ văn thư**

- Là cán bộ văn thư, tôi muốn kéo file Word vào phần mềm và bắt đầu rà soát với rất ít cấu hình.
- Là cán bộ văn thư, tôi muốn nhận lại file Word bôi vàng kèm comment, để gửi cho lãnh đạo xem trên Word như bình thường.
- Là cán bộ văn thư, tôi muốn thêm/sửa/xoá các quy tắc riêng dạng văn bản, để AI hiểu thuật ngữ, ngữ cảnh và quy ước của cơ quan.
- Là cán bộ văn thư soạn tài liệu dài, tôi muốn biết còn bao lâu nữa xong, để quyết định chờ hay làm việc khác.

**Quản trị viên**

- Là quản trị viên, tôi muốn tắt được phần AI, để phần mềm vẫn chạy trên máy cấu hình thấp.
- Là quản trị viên, tôi muốn cài phần mềm không cần internet, để triển khai được trong mạng nội bộ tách biệt.

---

## 6. Yêu cầu chức năng

### P0 — Bắt buộc để phát hành

**F1. Mở tài liệu**
- Kéo-thả hoặc chọn từ hộp thoại, định dạng `.docx`
- ✅ Given file .docx hợp lệ, when người dùng thả vào cửa sổ, then phần mềm hiển thị tên file, số từ và số trang best-effort nếu metadata lần lưu gần nhất có sẵn
- ✅ Given file không phải .docx, when thả vào, then hiện thông báo nêu rõ định dạng được hỗ trợ, không crash

**F2. Soát lỗi — bộ kiểm tra cơ bản tương thích**
- Chỉ chạy khi LLM-only full review bị tắt; người dùng không chọn preset, không bật/tắt nhóm rule và không có từ điển/danh sách bỏ qua
- Chuẩn hoá Unicode NFC trước mọi xử lý
- Lỗi kỹ thuật: khoảng trắng thừa/thiếu, dấu câu sai vị trí, lặp từ
- Âm tiết không tồn tại trong tiếng Việt
- Từ ghép dễ nhầm (danh sách confusion set)
- Quy tắc viết hoa theo Nghị định 30/2020, Phụ lục II
- ✅ Given tài liệu 50 trang, when chạy tầng luật, then hoàn tất trong ≤ 2 giây
- ✅ Given tài liệu chứa `ngiên cứu`, when soát, then báo lỗi và đề xuất `nghiên cứu`
- ✅ Given phần mềm chạy trên máy không có GPU, when soát bằng tầng luật, then vẫn chạy đầy đủ

**F3. Quản lý quy tắc riêng**
- Settings có một tab `Quy tắc riêng`; người dùng có thể xem danh sách, thêm, sửa và xoá
- Mỗi mục chỉ gồm một đoạn prompt text; không phải regex, dictionary entry hoặc một công tắc detector
- Tổng text của mọi mục tối đa 4.000 ký tự; dữ liệu được chuẩn hoá NFC và lưu cục bộ
- Khi AI bật và sẵn sàng, các mục được ghép theo thứ tự bằng một dòng trống thành context chung cho lần rà soát
- Khi AI tắt/chưa sẵn sàng, các mục vẫn được lưu nhưng không ảnh hưởng bộ kiểm tra cơ bản; UI phải nói rõ điều này
- ✅ Given người dùng thêm/sửa/xoá quy tắc, when mở lại Settings, then danh sách phản ánh đúng dữ liệu đã lưu
- ✅ Given tổng nội dung vượt giới hạn, when lưu, then thao tác bị từ chối và dữ liệu cũ không đổi

**F4. Soát lỗi — tầng ngữ cảnh (LLM)**
- Có ba chế độ tương thích: **rule-only**, **AI filter** và **AI full review / LLM-only**. Khi model hỗ trợ, LLM-only được chọn mặc định, không chạy tầng luật và model tự tìm lỗi chính tả, trình bày, ngữ pháp hoặc chọn từ trong mọi block được hỗ trợ.
- Full review chia nội dung theo token và cấu trúc; không gửi file DOCX nhị phân hoặc toàn bộ văn bản trong một prompt
- Mỗi block được hỗ trợ là target đúng một lần; block lân cận có thể chỉ làm context và LLM không được báo finding trong vùng context-only
- Bật/tắt AI từ tab `AI cục bộ`; full review xuất hiện và được bật mặc định khi manifest khai báo capability này. Với GGUF nhập trực tiếp, UI phải ghi rõ đây là chế độ thử nghiệm chưa được benchmark/phê duyệt phát hành.
- ✅ Given tắt tầng LLM, when soát, then phần mềm chạy bình thường bằng tầng luật
- ✅ Given bật AI filter, when soát, then LLM không được tạo finding ngoài các candidate đã gửi
- ✅ Given bật AI full review, when soát, then mọi paragraph/table cell được hỗ trợ đều được đưa qua một chunk target hoặc được báo rõ là chưa rà
- ✅ Given LLM trả về đề xuất không khớp với văn bản gốc, when xử lý kết quả, then bỏ qua đề xuất đó và không ghi vào output
- ✅ Given một chunk timeout hoặc trả output sai schema, when xử lý, then engine chia đôi và retry tuần tự tối đa hai cấp; nếu vẫn lỗi thì kết quả mang trạng thái `partial` và nêu nguyên nhân. Given mọi attempt đều thất bại, then job báo lỗi; không trường hợp nào được hiển thị “không phát hiện lỗi” như thể đã rà đủ

**F5. Tiến độ và huỷ**
- Hiển thị stage/progress trong lúc đọc file, chạy bộ kiểm tra, gọi AI và xuất kết quả
- Cho phép huỷ; huỷ không thay đổi file nguồn, không để lại output tạm hoặc process mồ côi
- Coverage `partial` phải hiển thị rõ và không được đổi thành trạng thái “không phát hiện cảnh báo”

**F6. Xuất file Word có comment**
- Tạo **file mới**, không ghi đè bản gốc
- Chỗ sai bôi vàng, kèm comment chứa từ đúng và lý do
- Giữ nguyên toàn bộ định dạng, ảnh, bảng, header/footer của bản gốc
- ✅ Given file đã xuất, when mở bằng Microsoft Word, then không có cảnh báo lỗi file
- ✅ Given tài liệu gốc có comment sẵn, when xuất, then comment cũ được giữ nguyên

**F7. Cài đặt phần mềm và model offline**
- Bộ cài chạy được trên máy không có internet
- Model được nhập bằng package đã xác minh; build connected có thể cho tải chủ động từ endpoint allowlist và không gửi nội dung tài liệu
- ✅ Given máy hoàn toàn không có mạng, when cài đặt và chạy, then phần mềm hoạt động đầy đủ

> **Superseded từ PRD 0.2:** preview tài liệu, finding list, thao tác sửa/bỏ qua/undo trong app, quản lý từ điển, preset và checkbox từng nhóm rule không còn thuộc sản phẩm hiện hành. Người dùng xem cảnh báo và quyết định chỉnh sửa trong file DOCX kết quả bằng Microsoft Word.

### P1 — Nên có

- Mẫu/bộ quy tắc riêng theo loại văn bản
- Danh sách tài liệu mở gần đây
- Ngưỡng tin cậy cho tầng LLM
- Tuỳ chọn kiểu bỏ dấu (`hòa` / `hoà`) — mặc định chỉ cảnh báo khi không nhất quán
- Sửa quy tắc rồi soát lại không cần mở file lại
- Ước tính thời gian còn lại theo từng stage
- Xuất báo cáo lỗi ra Excel
- Cột lề biên tập với ký hiệu theo dòng

### P2 — Tính đến khi thiết kế, không làm ở v1

- Xuất dạng track changes để Accept/Reject trong Word
- Xử lý hàng loạt
- Đọc `.doc` (Word 97-2003)
- Nhận diện và chuyển bảng mã phông chữ cũ
- Tự động viết lại hoặc chấm điểm văn phong toàn đoạn
- Nhật ký thao tác phục vụ kiểm toán

---

## 7. Yêu cầu phi chức năng

| | Yêu cầu |
|---|---|
| **Hiệu năng** | Tầng luật ≤ 2s/50 trang. AI filter ≤ 3 phút/50 trang trên cấu hình khuyến nghị. Full review có benchmark/giới hạn riêng theo model, token budget và coverage; không kế thừa SLA của filter trước khi PoC đạt gate |
| **Bảo mật** | Không có nội dung tài liệu hoặc kết nối nền rời máy. Đường xử lý phải zero-egress; model provisioning connected được tách riêng và chỉ tới allowlist |
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
  └→ Tầng 1: detector âm tiết dùng dữ liệu ngôn ngữ đóng gói sẵn
  └→ Tầng 2: confusion set từ ghép
  └→ Tầng 3: rule engine viết hoa (NĐ 30)
  └→ Tầng 4a: AI filter chấm điểm các trường hợp nghi ngờ  ← tùy chọn
  └→ Tầng 4b: AI full review rà mọi block được hỗ trợ theo chunk token  ← tùy chọn
  └→ hợp nhất verdict + lỗi mới, loại trùng/chồng lấn
  └→ neo lỗi về paragraph bằng khớp chuỗi (KHÔNG dùng offset do LLM trả về)
  └→ tách run, chèn highlight + comment
  └→ đóng gói .docx mới
```

**Bốn quyết định kỹ thuật bắt buộc tuân thủ:**

1. **Không tin offset do LLM trả về.** Filter trả candidate ID đã có anchor; full review trả `{segment_id, chuỗi_sai, occurrence_index, đề_xuất, lý_do}`. Code ánh xạ segment về đúng paragraph rồi tự tìm chuỗi. Không khớp thì loại bỏ, không đoán.
2. **Discovery chỉ có trong full review và vẫn bị giới hạn.** AI filter chỉ trả đúng/sai kèm điểm tin cậy cho candidate. Full review có thể trả finding mới nhưng chỉ bằng schema, trong block target đã gửi, không trả path/XML/offset hoặc toàn bộ đoạn đã viết lại.
3. **Không đưa nguyên file vào một prompt.** Full review đi tuần tự qua các chunk được lập bằng tokenizer của model, chừa ngân sách cho prompt/output/safety margin và báo coverage theo chunk. “Toàn văn” nghĩa là mọi block thuộc phạm vi hỗ trợ đã được rà, không có nghĩa là mọi OOXML story đều được hỗ trợ.
4. **Không biến quy tắc riêng thành cấu hình detector.** Các mục CRUD chỉ được nối thành context text cho LLM; bộ kiểm tra cơ bản không đổi theo dữ liệu này.

---

## 9. Chỉ tiêu nghiệm thu

Đo trên bộ **20 tài liệu thật** do khách cung cấp và **chốt trước khi bắt đầu code**, đã được cán bộ đơn vị soát tay và đánh dấu lỗi.

| Chỉ tiêu | Ngưỡng đạt |
|---|---|
| Recall — tỷ lệ bắt đúng lỗi đã đánh dấu | ≥ 85% |
| Precision — tỷ lệ cảnh báo đúng | ≥ 90% |
| Tính toàn vẹn file xuất | 20/20 file mở được bằng Word, định dạng không đổi |
| Thời gian tầng luật | ≤ 2 giây cho tài liệu 50 trang |
| Coverage AI full review | 100% block được hỗ trợ, hoặc kết quả phải ghi rõ `partial` cùng số chunk đã rà/thất bại |

> ⚠️ **Điều khoản cần đưa vào hợp đồng:** chỉ tiêu trên đo với **bộ kiểm tra cơ bản cố định**, không bao gồm các quy tắc riêng do người dùng nhập ở mục F3. Quy tắc riêng là context hỗ trợ AI, không nằm trong phạm vi cam kết chất lượng. AI filter và AI full review phải có báo cáo benchmark/capability riêng; đạt gate filter không đồng nghĩa model được phê duyệt cho full review.

---

## 10. Rủi ro

| Rủi ro | Mức | Cách giảm |
|---|---|---|
| LLM offline không đủ tốt với tiếng Việt | **Cao** | PoC 2 tuần đo trước. Kiến trúc để tầng luật đứng độc lập, tắt LLM vẫn còn sản phẩm |
| Full review chậm, thiếu chunk hoặc sinh lỗi ngoài anchor | **Cao** | Chạy tuần tự; timeout local 300 giây/chunk; retry chia đôi tối đa hai cấp; GPU offload có CPU fallback; exact-anchor validation và benchmark riêng trước phát hành |
| Annotation OOXML làm hỏng định dạng hoặc không neo được finding | Cao | Clone-and-patch, exact-anchor revalidation, partial/error rõ ràng và golden corpus mở bằng Word |
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
| Có cần chia sẻ/export bộ quy tắc riêng giữa các máy ở phase sau không? | Thiết kế |
| Có cần nhật ký thao tác phục vụ kiểm toán không? | Khách |
| Ai chịu trách nhiệm duy trì nội dung quy tắc riêng phía khách? | Khách |

---

## 12. Lộ trình

| Giai đoạn | Nội dung | Thời gian |
|---|---|---|
| **PoC** | Đo Gemma 4 trên 20 file thật. Demo luồng docx → highlight + comment. Báo cáo số liệu và báo giá chính xác | 2 tuần |
| **Phase 1** | Bộ kiểm tra cơ bản cố định + output DOCX annotation, không phụ thuộc LLM | 4 tuần |
| **Phase 2** | CRUD quy tắc riêng, tầng LLM và các mục P1 | 2–3 tuần |
| **Phase 3** | Đào tạo, bàn giao, bảo hành | 1 tuần |

**Nguyên tắc phân kỳ:** kết thúc Phase 1 phải có một sản phẩm giao được, dùng được, không phụ thuộc vào việc tầng LLM có thành công hay không. Đây là biện pháp đẩy rủi ro lớn nhất ra khỏi đường găng.
