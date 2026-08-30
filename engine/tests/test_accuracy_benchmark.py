from __future__ import annotations

from soatvan.checking import Block, Preset, RuleEngine
from soatvan.checking.localization import localize_llm_edits

TEST_DOCUMENT_TEXT = """ỦY BAN NHÂN DÂN
HUYỆN MINH HÒA	CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
Độc lập - Tự do - Hạnh phúc
Số: 47/BC-UBND                         Minh Hòa, ngày 15 tháng 8 năm 2026
BÁO CÁO
Kết quả rà sóat công tác tiếp nhận, xữ lý và lưu trử hồ sơ hành chánh
Thực hiện Công văn số 128/CV-SNV ngày 03/8/2026 của sở Nội vụ về việc tăng cường kỉ cương hành chính, Ủy ban nhân dân huyện Minh Hòa báo cáo tình hìng triển khai tại các cơ quan, đơn vị. Nội dung báo cáo đựơc tổng hợp từ số liêu do 12 phòng ban và 08 xã, thị trấn cung câp trong khoảng thòi gian từ ngày 01/01 đến 31/7/2026.
I. KHÁI QUÁT TÌNH HÌNH
Trong 7 tháng đầu năm, bộ phận Một cửa đã tiếp nhận 18.420 hồ sơ, trong đó 16.975 hồ sơ được giãi quyết đúng hạng, 937 hồ sơ giải quyết trể và 508 hồ sơ đang trong thời hạn. Việc áp dụng phần mềm một cữa điện tử cơ bản ổn định, tuy nhiên một số cán bộ còn thao tác sai qui trình, nhập thiếu trừơng dữ liệu hoặc đính kèm văn bãn chưa đúng định dạng.
Công tác phối hợp giửa Phòng Nội vụ, Văn phòng Hội đồng nhân dân và Uỷ ban nhân dân huyện với các đơn vị cơ sở đã có chuyển biến. Một số xã đã chủ động bố chí nhân lực trực vào ngày cao điểm, hướng dẩn người dân kê khai biểu mẩu và tra cứu tiến độ. Dù vậy, tình trạng hồ sơ chuyễn vòng, trả lại nhiều lần và cập nhật kết qủa chậm vẩn còn xãy ra.
II. MỤC ĐÍCH, YÊU CẦU
Đợt rà soát nhằm đánh giá đúng thực trạn, làm rỏ nguyên nhân và đề suất giải pháp khắc phục. Các đơn vị phải báo cáo trung thực, không che dấu thiếu sót; số liệu cần đựơc đối chiếu giửa sổ theo dõi, hệ thống điện tử và hồ sơ giấy trước khi gửi về phòng Nội vụ.
III. KẾT QUẢ THỰC HIỆN
1. Công tác chỉ đạo, điều hành
Ủy ban nhân dân huyện đã ban hành 06 văn bãn chỉ đạo, tổ chức 03 cuộc họp giao ban và 02 lớp tập huấn nghiệp vu. Người đứng đầu các cơ quan cơ bản quan tâm, song việc phân công cán bộ tại vài đơn vị còn chưa cụ thễ, dẩn đến tình trạng đùn đẫy trách nhiệm khi hồ sơ phát sinh vướng mắc.
1.Các phòng chuyên môn đã xây dựng kế họach kiểm tra nội bộ, nhưng 04 đơn vị gởi kế hoạch trể hơn thời hạng qui định từ 05 đến 12 ngày.
2.Bộ phận tiếp nhận có niêm yết thủ tục, lệ phí và thòi gian giãi quyết; một số bảng hướng dẩn bị củ, chử nhỏ và thiếu mã QR tra cứu.
3.Việc quán triệt thái độ giao tiếp đựơc thực hiện qua họp giao ban hằngtháng, tuy nhiên chưa có biểu mẩu ghi nhận ý kiến phản hồi thống nhứt.
2. Tiếp nhận và phân loại hồ sơ
Qua kiểm tra ngẩu nhiên 1.250 hồ sơ, đoàn rà soát phát hiện 83 trường hợp ghi sai tên thủ tục, 57 trường hợp thiếu ngày hẹn trả và 21 trường hợp sắp sếp thành phần hồ sơ không đúng thứ tự. Một số phiếu tiếp nhận bị tẩy xoá, chử ký cán bộ không rỏ hoặc đóng mộc chồng lên nội dung.
4.Hồ sơ trực tuyến: tỷ lệ khai báo sai số căn cước còn cao, nhiều tập tin đặt tên không dấu như giayphep, donnghi, xacnhan nên khó tìm kíêm.
5.Hồ sơ trực tiếp: cán bộ đôi lúc hướng dẩn bằng miệng, không phát phiếu yêu cầu bổ sung, làm người dân phải đi lạị nhìêu lần.
6.Hồ sơ liên thông: thông tin chuyển giửa các đơn vị chưa đầy đủ, nhất là địa chỉ thư điện tử và số điện thọai của người liên hệ.
3. Giải quyết và trả kết quả
Phần lớn hồ sơ được xử lý theo đúng thẩm quyền. Tuy nhiên, 46 hồ sơ đất đai bị kéo dài do phải xác minh nguồn gốc; 19 hồ sơ hộ tịch chậm vì lổi kết nối cơ sở dử liệu; 08 hồ sơ xây dựng bị chuyễn trả do bản vẻ thiếu chử ký. Các trường hợp quá hạng chưa đựơc xin lổi bằng văn bản đầy đủ.
Tại thời điểm kiểm tra, một số kết quả đã ký nhưng chưa cập nhựt trạng thái trên hệ thống, khiến tin nhắn thông báo không đựơc gởi kịpthời. Sổ bàn giao kết quả ghi chép còn sơ sài, có trang không ghi giờ nhận và không có chử kí của người nhận.
IV. TỔNG HỢP MỘT SỐ TỒN TẠI
Bảng dưới đây tổng hợp các nhóm sai sót nỗi bật được phát hiện qua kiểm tra hồ sơ và phỏng vấn cán bộ. Số liệu mang tính đối chiếu nội bộ, chưa dùng để kết luận trách nhiệm cá nhơn.
STT	Nội dung tồn tại	Số trường hợp	Đơn vị liên quan
1	Nhập sai hoặc thiếu dử liệu cá nhân	57	Bộ phận một cữa
2	Chuyển hồ sơ trể so với qui trình	34	Phòng chuyên môn
3	Tập tin đính kèm bị mờ, sai định giạng	29	Các xã, thị chấn
4	Phiếu hẹn thiếu chử ký hoặc con dấu	21	Văn thư - tiếp nhận
5	Không cập nhựt kết quả lên hệ thống	18	Cán bộ thụ lý
6	Lưu trử hồ sơ chưa đúng danh mục	15	Kho lưu trử
1. Nguyên nhân chủ quan
Một bộ phận cán bộ chưa nắm vửng quy trình, ngại tra cứu văn bãn mới và còn thói quen xữ lý hồ sơ theo kinh ngiệm. Việc tự kiểm tra trước khi trình ký chưa đựơc duy trì thường xuyên; người phụ trách đôi lúc chỉ rà soát hình thức mà bỏ xót thông tin quan trọn.
Khối lượng công việc tăng nhưng chưa bố chí người thay thế khi cán bộ nghỉ phép, đi công tác hoặc tham gia tập huấn.
Trách nhiệm phối hợp chưa đựơc qui định rỏ; hồ sơ vướng mắc thường phải trao đổi qua nhiều đầu mối.
Kỷ năng soạn thảo, đặt tên tập tin và quản lí thư mục của một số công chức còn hạng chế.
2. Nguyên nhân khách quan
Hạ tầng mạng tại các xã vùng xa còn chập chờn; thiết bị quét tài liệu đã củ, cho chất lượng hình ãnh thấp. Một số biểu mẩu do cấp trên ban hành thay đổi nhanh nhưng phần mềm chưa cập nhựt đồng bộ, dẩn đến chênh lệch giửa hướng dẩn giấy và biểu mẩu điện tử.
Ngoài ra, người dân chưa quen nộp hồ sơ trực tuyến, thường chụp tài liệu bằng điện thọai, thiếu ánh sáng hoặc cắt mất góc. Việc xác thực tài khoản đôi khi không thành công vì số thuê bao không trùng với thông tin căn cước công dâng.
V. NHIỆM VỤ VÀ GIẢI PHÁP KHẮC PHỤC
1. Chuẩn hóa nghiệp vụ
Các cơ quan, đơn vị khẩn trương rà soát toàn bộ qui trình nội bộ, đối chiếu với thủ tục hành chính đã công bố và hoàn thàh việc cập nhựt trước ngày 15/9/2026. Mọi thay đổi phải đựơc thông báo công khai, đồng thời gởi bản điện tử về Văn phòng huyện để theo giỏi.
Tổ chức tập huấn lại cho cán bộ về cách tiếp nhận, số hóa, luân chuyễn và lưu trử hồ sơ; ưu tiên người mới đựơc phân công.
Ban hành danh sách kiểm tra bắt buộc trước khi trình ký, gồm thành phần hồ sơ, thời hạng, thẩm quyền và định giạng tập tin.
Thực hiện đối chiếu dử liệu vào cuối mổi ngày; sai lệch phải đựơc sữa chửa và ghi nhận nguyên nhơn trong sổ theo dõi.
2. Nâng cao chất lượng phục vụ
Cán bộ tiếp nhận phải hướng dẩn một lần, rỏ ràng, dể hiểu và cấp phiếu bổ sung theo đúng mẩu. Nghiêm cấm yêu cầu thêm giấy tờ ngoài qui định hoặc tự ý kéo dài thòi gian. Đối với người cao tuổi, người khuyết tật và công dân không có thiết bị số, cần bố chí bàn hổ trợ riêng.
7.Niêm yết số điện thọai đường dây nóng, địa chỉ thư điện tử và mã QR phản ánh kiến nghị tại vị trí dể quan sát.
8.Gởi tin nhắn xin lổi đối với hồ sơ trể hạng, nêu rỏ nguyên nhân và thời gian dự kiến trả kết qủa.
9.Khảo sát mức độ hài lồng tối thiểu 30% số người đến giao dịch mổi tháng; tổng hợp ý kiến theo từng lỉnh vực.
3. Bảo đảm điều kiện kỷ thuật
Phòng Văn hóa và Thông tin chủ trì kiểm tra đường truyền, cấu hình máy trạm và quyền truy cập tài khoản. Thiết bị quét hư hỏng phải đựơc sữa hoặc thay thế kịpthời; phần mềm diệt vi-rút cần cập nhựt định kì và không dùng chung mật khẫu giửa nhiều cán bộ.
Đề nghị nhà cung cấp hoàn thiện chức năng cảnh báo hồ sơ sắp quá hạng, tự động kiểm tra trừơng bắt buộc và ghi nhật kí thao tác. Khi hệ thống gặp sự cố, đơn vị vận hành phải thông báo ngay, không để cán bộ tự chuyễn sang quy trình giấy mà thiếu biên bãn xác nhận.
VI. TỔ CHỨC THỰC HIỆN
1. Phân công trách nhiệm
Phòng Nội vụ là cơ quan đầu mối, có trách nhiệm hướng dẩn, đôn đôc và tổng hợp kết qủa thực hiện. Văn phòng Hội đồng nhân dân và Ủy ban nhân dân huyện theo giỏi tiến độ xử lý hồ sơ trên hệ thống, kịp thời nhắt nhở các đơn vị có tỷ lệ trể hạng cao.
Thủ trưởng các phòng ban và Chủ tịch Ủy ban nhân dân các xã, thị trấn chịu trách nhiệm trước Chủ tịch huyện nếu để tái diển sai sót kéo dài. Báo cáo khắc phục phải gởi trước ngày 25 hằngtháng, nêu rỏ số việc đã hoàn thành, việc chưa hoàn thành và nguyên nhơn.
2. Kiểm tra, giám sát
Từ tháng 9/2026, đoàn kiểm tra liên ngành sẻ kiểm tra đột suất tối thiểu 02 đơn vị mổi tháng. Nội dung kiểm tra gồm hồ sơ giấy, dử liệu điện tử, sổ theo dõi, việc niêm yết thủ tục và thái độ phục vụ. Kết luận kiểm tra đựơc gửi cho đơn vị trong vòng 05 ngày làm việc.
Đơn vị được kiểm tra phải cung câp tài liệu đầy đủ, không tự ý sữa chửa hồ sơ sau khi có quyết định kiểm tra.
Sai sót lần đầu được yêu cầu chấn chĩnh; trường hợp tái phạm hoặc gây phiền hà sẽ xem xét trách nhiệm người đứng đầu.
Kết qủa khắc phục được dùng để xếp lọai công chức cuối năm.
VII. KẾT LUẬN VÀ KIẾN NGHỊ
Công tác tiếp nhận và giãi quyết thủ tục hành chính trên địa bàn huyện đã có chuyển biến tích cực, nhưng vẩn còn nhiều hạng chế về kỷ năng, dử liệu và phối hợp. Ủy ban nhân dân huyện đề nghị các đơn vị nghiêm túc khắc phục, coi đây là nhiệm vụ thừơng xuyên, gắn với trách nhiệm phục vụ nhơn dân.
Đề nghị Sở Nội vụ tiếp tục hổ trợ tài liệu tập huấn, thống nhứt biểu mẩu báo cáo và kiến nghị cấp có thẩm quyền nâng câp phần mềm. Trường hợp phát sinh khó khăng vượt thẩm quyền, các đơn vị cần báo cáo bằng văn bãn để đựơc hướng dẩn, không tự ý giãi quyết trái qui định.
Trên đây là báo cáo kết quả rà sóat công tác tiếp nhận, xữ lý và lưu trử hồ sơ hành chánh của Ủy ban nhân dân huyện Minh Hòa./."""


def test_rule_engine_benchmark_on_heavily_erroneous_text() -> None:
    blocks = [
        Block(f"document:p{idx}", line.strip())
        for idx, line in enumerate(TEST_DOCUMENT_TEXT.splitlines())
        if line.strip()
    ]
    findings = RuleEngine().check(blocks, Preset.STANDARD, limit=1000)
    # The rule engine should now catch a massive amount of deterministic errors (>= 100)
    assert len(findings) >= 100

    found_sources = {f.source_text for f in findings}
    # Check essential error types are detected.
    # After dictionary integration, many errors are caught as individual syllables
    # rather than multi-word CONFUSIONS entries.
    assert "xữ" in found_sources  # dictionary catches invalid syllable
    assert "trử" in found_sources  # from "lưu trử"
    assert "hành chánh" in found_sources  # CONFUSIONS: regional variant
    assert "đựơc" in found_sources  # dictionary catches invalid syllable
    assert "lổi" in found_sources  # dictionary catches invalid syllable
    assert "thống nhứt" in found_sources  # CONFUSIONS: regional variant
    assert "nhơn dân" in found_sources  # CONFUSIONS: regional variant
    assert "hằngtháng" in found_sources  # CONFUSIONS: sticky keys
    assert "kịpthời" in found_sources  # CONFUSIONS: sticky keys


def test_localization_accepts_dialect_and_spacing_variations() -> None:
    # Dialect rime alterations
    edits = localize_llm_edits("nhơn dân", "nhân dân", "word_choice")
    assert [(e[1], e[2]) for e in edits] == [("nhơn", "nhân")]

    edits = localize_llm_edits("thống nhứt", "thống nhất", "word_choice")
    assert [(e[1], e[2]) for e in edits] == [("nhứt", "nhất")]

    edits = localize_llm_edits("hài lồng", "hài lòng", "spelling")
    assert [(e[1], e[2]) for e in edits] == [("lồng", "lòng")]

    # Spacing and typos
    edits = localize_llm_edits("hằngtháng", "hằng tháng", "technical")
    assert [(e[1], e[2]) for e in edits] == [("hằngtháng", "hằng tháng")]
