# Alpha Forge

Bộ khung Python phục vụ vòng đời nghiên cứu alpha trên nền tảng WorldQuant BRAIN: sinh biểu thức, gửi mô phỏng, thu thập chỉ số, chấm điểm, lọc tương quan và ghi nhớ lịch sử nghiên cứu.

## 1. Kiến trúc

```text
BRAIN
  ↓
Historical Alpha Intelligence ─────┐
  ↓                               │
Submitted Alpha History           │
  ↓                               │
Fingerprint / Structure            │
  ↓                               │
Research Memory                   │
  ↑                               │
Research Project → Hypothesis → Experiment
  ↓
Generator
  ↓
Simulation → Score → Correlation → Submission
```

Các lớp chính hiện có:

```text
client.py / alphaforge/brain       Kết nối và xác thực BRAIN
engine.py / alphaforge/generator   Sinh expression
simulation pipeline                Mô phỏng và chấm điểm
correlation pipeline               Tương quan self và product
alphaforge/history                 Lịch sử alpha đã submit
research_store.py                  Research Project và Alpha Lineage
history_report.py                  Phân tích lịch sử nghiên cứu
history_web.py                     Dashboard chỉ đọc
```

## 2. Historical Alpha Intelligence

Hệ thống có thể lấy trực tiếp alpha đã submit của tài khoản hiện tại từ endpoint `/users/self/alphas`, theo thứ tự mới nhất trước. Dữ liệu được lưu cục bộ vào bảng `historical_alphas` để có thể phân tích lại mà không gọi BRAIN cho mỗi lần xem báo cáo.

Mỗi bản ghi giữ:

```text
Alpha ID
Ngày submit
Trạng thái
Region
Universe
Delay
Expression
Sharpe
Fitness
Returns
Turnover
Margin
Expression fingerprint
Expression chuẩn hóa
Danh sách toán tử
Danh sách trường dữ liệu
```

Cả trạng thái thành công và không thành công đều được giữ lại. Mục tiêu là tránh việc chỉ lưu alpha sống sót và giúp nhận diện những hướng nghiên cứu đã được thử nhiều lần.

Fingerprint hiện dùng dạng chuẩn hóa cấu trúc, trong đó các số như cửa sổ thời gian được thay bằng ký hiệu chung. Vì vậy các biểu thức chỉ khác lookback có thể được nhận diện là cùng một cấu trúc nghiên cứu.

## 3. Quét lịch sử

Quét 30 ngày gần nhất:

```bash
python history_cli.py
```

Quét một khoảng cụ thể:

```bash
python history_cli.py --start-date 2026-08-01 --end-date 2026-08-22
```

Kết quả gồm số lượng alpha, trạng thái, region, universe, Sharpe trung bình, Fitness trung bình, Turnover trung bình và các chỉ số tốt nhất.

## 4. Báo cáo cấu trúc nghiên cứu

Có thể tạo báo cáo từ SQLite mà không đăng nhập BRAIN:

```bash
python -c "from history_report import build_report; import json; from config import load_settings; print(json.dumps(build_report(load_settings().db_path), ensure_ascii=False, indent=2))"
```

Báo cáo bổ sung:

```text
Toán tử được dùng nhiều nhất
Trường dữ liệu được dùng nhiều nhất
Cấu trúc expression lặp lại
```

Đây là lớp dữ liệu đầu vào cho việc quyết định hướng nghiên cứu tiếp theo.

## 5. Dashboard

Mở dashboard lịch sử:

```bash
python history_web.py
```

Sau đó truy cập `http://127.0.0.1:8000`.

Dashboard chỉ đọc SQLite và không gọi BRAIN. Việc quét dữ liệu vẫn được thực hiện bằng CLI để tránh dashboard tự phát sinh các yêu cầu mô phỏng hoặc API không kiểm soát.

## 6. Research Layer

Research Layer ghi lại:

```text
Research Project
      ↓
Hypothesis
      ↓
Experiment
      ↓
Experiment Variant
      ↓
Alpha Lineage
```

Mục tiêu là để một alpha không còn chỉ là một expression đơn lẻ mà trở thành kết quả của một quá trình nghiên cứu có thể truy nguyên.

## 7. Vòng đời hoàn chỉnh

```text
1. Quét lịch sử BRAIN
2. Phân tích những gì đã thử
3. Xác định vùng nghiên cứu còn thiếu
4. Tạo Research Project
5. Viết Hypothesis
6. Thiết kế Experiment
7. Sinh các Variant
8. Mô phỏng
9. Chấm điểm
10. Kiểm tra self correlation và product correlation
11. Chọn alpha đủ tốt
12. Submit
13. Ghi alpha mới vào Research Memory
14. Lần nghiên cứu tiếp theo lại sử dụng lịch sử này
```

Điểm quan trọng là Historical Intelligence chạy trước Generator trong quy trình nghiên cứu, thay vì chỉ dùng lịch sử sau khi đã sinh alpha.

## 8. Cài đặt

```bash
git clone <địa chỉ kho>
cd alpha-forge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Điền `BRAIN_EMAIL` và `BRAIN_PASSWORD` vào `.env`. Tệp này không được commit.

## 9. Pipeline hiện tại

Sinh:

```bash
python -m alphaforge.cli generate --strategy template --limit 300 --tag lot1
```

Chạy mô phỏng:

```bash
python -m alphaforge.cli run --concurrency 3 --limit 300
```

Chấm điểm và tương quan:

```bash
python -m alphaforge.cli score
python -m alphaforge.cli correlate --max-self 0.7
```

Xuất alpha xếp hạng cao:

```bash
python -m alphaforge.cli report --top 50 --out top_alphas.csv
```

## 10. Nguyên tắc vận hành

Không tự động sinh vô hạn hoặc gửi vô hạn. Simulation và API của BRAIN đều có giới hạn. Lịch sử cũng được lưu cục bộ để giảm số lần gọi API.

Không coi Sharpe cao là bằng chứng đủ để submit. Cần xem Fitness, Turnover, Drawdown, Margin, self correlation, product correlation và mức độ trùng lặp với nghiên cứu trước.

Bộ sinh hiện tại vẫn dựa trên mẫu và tổ hợp toán tử. Mô hình ngôn ngữ có thể được tích hợp ở lớp đề xuất giả thuyết hoặc sinh biến thể sau khi Research Memory hoạt động ổn định; không bắt buộc phải dùng mô hình ngôn ngữ để chạy pipeline cơ bản.

## 11. Kiểm thử

Chạy kiểm thử Research Layer:

```bash
pytest test_research.py
```

Chạy kiểm thử Historical Alpha Intelligence:

```bash
pytest test_history_intelligence.py
```

## 12. Giấy phép

MIT. Xem tệp LICENSE.
