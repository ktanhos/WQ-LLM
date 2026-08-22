# Quy trình vận hành chi tiết

## 1. Vòng đời một biểu thức

| Trạng thái | Ý nghĩa | Chuyển tiếp |
| --- | --- | --- |
| PENDING | Đã sinh, đang chờ mô phỏng | `claim_pending` đưa sang RUNNING |
| RUNNING | Một luồng đang gửi mô phỏng | Thành công sang SIMULATED, lỗi tạm thời quay lại PENDING |
| SIMULATED | Có chỉ số nhưng chưa chấm điểm | Bộ chấm điểm đưa sang PASSED hoặc REJECTED |
| PASSED | Đạt toàn bộ ngưỡng chỉ số | Bước tương quan có thể hạ xuống REJECTED |
| REJECTED | Không đạt ít nhất một ngưỡng | Kết thúc, trừ khi chấm lại với ngưỡng mới |
| FAILED | Biểu thức sai cú pháp hoặc hết số lần thử | Kết thúc |
| SUBMITTED | Người dùng đã nộp trên nền tảng | Cập nhật thủ công |

Tiến trình bị ngắt giữa chừng để lại bản ghi kẹt ở RUNNING. Lệnh `reset` hoặc cờ `--reset-stuck` đưa các bản ghi đó về PENDING. Mỗi lần nhận bản ghi khỏi hàng đợi làm tăng `attempts`; vượt `--max-attempts` thì bản ghi chuyển sang FAILED thay vì thử lại mãi.

## 2. Thứ tự chạy khuyến nghị

1. `auth` một lần mỗi ngày làm việc để chắc chắn phiên còn hiệu lực.
2. `history scan` để nạp lịch sử alpha đã nộp. Bước này nên chạy **trước** khi sinh lô mới, vì bộ sinh dùng lịch sử để tránh lặp lại hướng đã bão hòa.
3. `fields` cho từng cặp khu vực và tập dữ liệu cần khảo sát. Kết quả nằm trong kho cục bộ nên chỉ cần tải lại khi nền tảng bổ sung dữ liệu mới.
4. `history gaps` để xem khoảng trống nào đáng khảo sát, rồi tạo dự án và giả thuyết tương ứng bằng nhóm lệnh `research`.
5. `generate` theo từng lô có gắn thẻ. Gắn thẻ giúp truy vết lô nào cho tỷ lệ đạt cao.
6. `run` với mức đồng thời thấp trước, quan sát nhật ký, sau đó mới tăng dần.
7. `score` sau khi lô chạy xong. Có thể chạy lại nhiều lần với ngưỡng khác nhau vì bước này không tốn hạn mức mô phỏng.
8. `correlate` chỉ trên nhóm PASSED.
9. `report` để xuất danh sách xem xét thủ công.
10. Nộp alpha **thủ công** trên nền tảng, rồi chạy lại `history scan` để vòng nghiên cứu khép kín.

## 3. Vì sao tách chấm điểm khỏi mô phỏng

Mô phỏng tốn hạn mức máy chủ và không thể lặp lại miễn phí. Chấm điểm chỉ đọc dữ liệu đã lưu. Tách hai bước cho phép thay đổi ngưỡng và chạy lại toàn bộ tập kết quả trong vài giây, thay vì phải mô phỏng lại từ đầu.

Cùng lý do đó, bước `correlate` tách riêng và chỉ chạy trên nhóm PASSED: điểm cuối tương quan tốn tài nguyên máy chủ, gọi cho mọi alpha là lãng phí vì phần lớn đã bị loại từ trước.

## 4. Cách đọc điểm tổng hợp

Điểm tổng hợp là tổ hợp tuyến tính có trọng số của Sharpe, Fitness, Margin và phần phạt vòng quay danh mục. Nó chỉ phục vụ việc xếp thứ tự ưu tiên xem xét. Hai biểu thức cùng điểm có thể rất khác nhau về bản chất, nên vẫn phải mở từng biểu thức để đánh giá tính hợp lý về mặt kinh tế.

Một cảnh báo cần lưu ý: chọn ra biểu thức tốt nhất từ hàng nghìn phép thử trên cùng một tập dữ liệu là hình thức khai thác dữ liệu. Điểm cao trong mẫu không bảo đảm hiệu năng ngoài mẫu. Càng chạy nhiều biểu thức thì rủi ro này càng lớn, và số lượng phép thử nên được ghi nhận khi báo cáo kết quả. Bảng `historical_alphas` cùng `alphas` chính là nơi lưu con số đó.

## 5. Trí nhớ nghiên cứu hoạt động thế nào

`history scan` gọi điểm cuối `/users/self/alphas` qua BrainClient, phân trang theo thứ tự mới nhất trước và dừng khi chạm mốc ngày bắt đầu. Mỗi bản ghi được tính vân tay cấu trúc rồi ghi vào bảng `historical_alphas`.

Nguyên tắc bất di bất dịch: **không làm mất alpha vì một bản ghi lỗi**. Bản ghi thiếu ngày nộp vẫn được giữ lại và đếm vào phần chẩn đoán, vì không thể khẳng định nó nằm ngoài khoảng quan tâm. Bản ghi hỏng hoàn toàn được bỏ qua và đếm riêng, không hủy cả lượt quét. Phần `diagnostics` trong kết quả cho biết chính xác điều gì đã xảy ra.

`ResearchMemory` đọc cả `historical_alphas` lẫn `alphas` đã có kết quả, gom theo họ cấu trúc, rồi tính một trọng số cho mỗi họ:

- Họ chưa đủ cỡ mẫu: trọng số 1, giữ nguyên ưu tiên.
- Họ đủ cỡ mẫu, trung vị Sharpe dưới ngưỡng: trọng số giảm, mức giảm tỷ lệ với độ tin cậy của cỡ mẫu.
- Họ đã thử nhiều mà chưa có alpha nào đạt: giảm thêm một nửa.
- Họ đủ cỡ mẫu và trung vị Sharpe trên ngưỡng: trọng số tăng.

Trọng số **không bao giờ bằng không**. Một họ từng cho kết quả kém vẫn có thể tốt trở lại khi thị trường đổi trạng thái, nên khóa cứng không gian tìm kiếm theo dữ liệu quá khứ là sai. Trọng số dưới 1 hoạt động như xác suất giữ lại ứng viên, không phải bộ lọc tuyệt đối.

## 6. Thiên lệch sống sót

Trí nhớ nghiên cứu lưu **mọi** trạng thái: PASSED, REJECTED, FAILED và SUBMITTED. Nếu chỉ lưu alpha đạt thì mọi họ cấu trúc đều có tỷ lệ đạt bằng một, và không còn phân biệt được họ nào thực sự tốt.

Vì lý do đó, `history scan` mặc định lấy alpha đã nộp nhưng có cờ `--include-unsubmitted` để lấy cả alpha chưa nộp khi cần bức tranh đầy đủ hơn.

## 7. Vì sao dùng trung vị thay vì trung bình

Phân phối Sharpe của alpha có đuôi dày. Một alpha Sharpe 6 lọt vào nhóm mười alpha sẽ kéo trung bình lên trên mọi quan sát thực tế trong nhóm, khiến con số không mô tả đúng bất kỳ alpha nào.

Mọi báo cáo trong `history/analyzer.py` vì vậy đều trả về trung vị, phân vị 25, phân vị 75 và cỡ mẫu. Trung bình vẫn được giữ lại để đối chiếu, nhưng không dùng để xếp hạng. Họ cấu trúc có ít hơn `min_sample_for_ranking` alpha thì chỉ được liệt kê chứ không được xếp hạng.

## 8. Khả năng tái lập

Mỗi alpha lưu kèm toàn bộ thiết lập mô phỏng đã dùng trong cột `settings_json`, chứ không tham chiếu tới cấu hình hiện tại. Nhờ vậy đổi `config/settings.yaml` về sau không làm mất khả năng tái hiện một thí nghiệm cũ.

Mỗi `Experiment` cũng lưu bản sao thiết lập của riêng nó. Khóa khử trùng lặp gồm cả biểu thức lẫn thiết lập, nên cùng một biểu thức chạy ở hai khu vực khác nhau là hai thí nghiệm riêng biệt.

Bảng `alpha_lineage` ghi quan hệ cha con, chiến lược sinh, kiểu biến đổi và nguồn gốc. Alpha sinh ra từ một alpha lịch sử cũng được ghi phả hệ với `source = "historical"`.

## 9. Giới hạn tần suất

`concurrency` trong cấu hình là **trần**, không phải mức cố định. Khi máy chủ trả về 429, `AdaptiveLimiter` tạm dừng toàn bộ hàng đợi theo thời gian máy chủ yêu cầu và giảm một nửa số mô phỏng song song. Sau mười lượt thành công liên tiếp, mức đồng thời được nới thêm một chỗ, không bao giờ vượt trần.

Cơ chế này cần thiết vì nếu chỉ luồng gặp lỗi ngủ, các luồng còn lại vẫn gửi với nhịp cũ và tiếp tục bị từ chối, làm hàng đợi chậm đi thay vì nhanh lên.

## 10. Điểm mở rộng

Thêm mẫu biểu thức: bổ sung vào `alphaforge/generator/templates.py`. Mẫu mới được nhận diện tự động.

Thay chiến lược sinh: thêm phương thức `_generate_<tên>` trong `alphaforge/generator/engine.py` và khai báo tên đó trong `generate`. Đầu ra vẫn phải là danh sách chuỗi để phần còn lại của quy trình không đổi.

Thay ngưỡng chấm điểm: sửa `config/settings.yaml` rồi chạy `score --rescore-all`.

Thêm nhà cung cấp mô hình ngôn ngữ: kế thừa `alphaforge/llm/base.py:LLMProvider`, cài đặt `complete` và `is_available`, rồi đăng ký trong `get_provider`.

Chuyển sang chạy nhiều máy: thay `alphaforge/storage/db.py` bằng lớp tương đương dùng PostgreSQL. SQLite khóa toàn bộ tệp khi ghi nên không phù hợp cho nhiều tiến trình trên các máy khác nhau.

## 11. Những việc hệ thống cố ý không làm

Không tự nộp alpha. Mô phỏng đạt ngưỡng chỉ đưa alpha vào danh sách đề xuất; quyết định nộp thuộc về người nghiên cứu.

Không để mô hình ngôn ngữ điều khiển hệ thống. Nó chỉ sinh văn bản để người đọc.

Không tự chạy hàng nghìn mô phỏng. Mọi lệnh đều có giới hạn số lượng rõ ràng.

Không gọi BRAIN từ bảng theo dõi. Giao diện web chỉ đọc SQLite.
