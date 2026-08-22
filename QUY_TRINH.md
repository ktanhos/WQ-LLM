# Quy trình vận hành chi tiết

## 1. Vòng đời một biểu thức

| Trạng thái | Ý nghĩa | Chuyển tiếp |
| --- | --- | --- |
| PENDING | Đã sinh, đang chờ mô phỏng | claim_pending đưa sang RUNNING |
| RUNNING | Một luồng đang gửi mô phỏng | Thành công sang SIMULATED, lỗi tạm thời quay lại PENDING |
| SIMULATED | Có chỉ số nhưng chưa chấm điểm | Bộ chấm điểm đưa sang PASSED hoặc REJECTED |
| PASSED | Đạt toàn bộ ngưỡng chỉ số | Bước tương quan có thể hạ xuống REJECTED |
| REJECTED | Không đạt ít nhất một ngưỡng | Kết thúc, trừ khi chấm lại với ngưỡng mới |
| FAILED | Biểu thức sai cú pháp hoặc hết số lần thử | Kết thúc |
| SUBMITTED | Người dùng đã nộp trên nền tảng | Cập nhật thủ công |

Tiến trình bị ngắt giữa chừng sẽ để lại bản ghi kẹt ở RUNNING. Lệnh `reset`
hoặc cờ `--reset-stuck` đưa các bản ghi đó về PENDING.

## 2. Thứ tự chạy khuyến nghị

1. `auth` một lần mỗi ngày làm việc để chắc chắn phiên còn hiệu lực.
2. `fields` cho từng cặp khu vực và tập dữ liệu cần khảo sát. Kết quả nằm trong kho cục bộ nên chỉ cần tải lại khi nền tảng bổ sung dữ liệu mới.
3. `generate` theo từng lô có gắn thẻ. Gắn thẻ giúp truy vết lô nào cho tỷ lệ đạt cao.
4. `run` với mức đồng thời thấp trước, quan sát nhật ký, sau đó mới tăng dần.
5. `score` sau khi lô chạy xong. Có thể chạy lại nhiều lần với ngưỡng khác nhau vì bước này không tốn hạn mức mô phỏng.
6. `correlate` chỉ trên nhóm PASSED.
7. `report` để xuất danh sách xem xét thủ công.

## 3. Vì sao tách chấm điểm khỏi mô phỏng

Mô phỏng tốn hạn mức máy chủ và không thể lặp lại miễn phí. Chấm điểm chỉ đọc
dữ liệu đã lưu. Tách hai bước cho phép thay đổi ngưỡng và chạy lại toàn bộ tập
kết quả trong vài giây, thay vì phải mô phỏng lại từ đầu.

## 4. Cách đọc điểm tổng hợp

Điểm tổng hợp là tổ hợp tuyến tính có trọng số của Sharpe, Fitness, Margin và
phần phạt vòng quay danh mục. Nó chỉ phục vụ việc xếp thứ tự ưu tiên xem xét.
Hai biểu thức cùng điểm có thể rất khác nhau về bản chất, nên vẫn phải mở từng
biểu thức để đánh giá tính hợp lý về mặt kinh tế.

Một cảnh báo cần lưu ý: chọn ra biểu thức tốt nhất từ hàng nghìn phép thử trên
cùng một tập dữ liệu là hình thức khai thác dữ liệu. Điểm cao trong mẫu không
bảo đảm hiệu năng ngoài mẫu. Càng chạy nhiều biểu thức thì rủi ro này càng lớn,
và số lượng phép thử nên được ghi nhận khi báo cáo kết quả.

## 5. Điểm mở rộng

Thêm mẫu biểu thức: bổ sung vào `alphaforge/generator/templates.py`. Mẫu mới
được nhận diện tự động, không cần sửa nơi khác.

Thay chiến lược sinh: thêm phương thức `_generate_<tên>` trong
`alphaforge/generator/engine.py` và khai báo tên đó trong `generate`. Nếu muốn
dùng mô hình ngôn ngữ để sinh biểu thức thì đây là nơi cắm vào, đầu ra vẫn phải
là danh sách chuỗi để phần còn lại của quy trình không đổi.

Thay ngưỡng chấm điểm: sửa `config/settings.yaml` rồi chạy `score --rescore-all`.

Chuyển sang chạy nhiều máy: thay `alphaforge/storage/db.py` bằng lớp tương đương
dùng PostgreSQL. SQLite khóa toàn bộ tệp khi ghi nên không phù hợp cho nhiều
tiến trình trên các máy khác nhau.
