# Alpha Forge

Bộ khung Python phục vụ toàn bộ vòng đời nghiên cứu alpha trên nền tảng WorldQuant BRAIN: sinh biểu thức, gửi mô phỏng, thu thập chỉ số, chấm điểm, lọc tương quan và theo dõi tiến độ qua giao diện web.

## 1. Kiến trúc

```
alphaforge/
  config.py            Nạp cấu hình từ biến môi trường và tệp YAML
  brain/client.py      Lõi kết nối BRAIN: xác thực, mô phỏng, đọc chỉ số, tra cứu trường dữ liệu
  brain/errors.py      Phân loại lỗi để lớp trên quyết định thử lại
  storage/db.py        Kho SQLite lưu biểu thức, phiên chạy, chỉ số, trạng thái
  generator/           Sinh biểu thức theo mẫu, theo trường dữ liệu và theo tổ hợp toán tử
  pipeline/runner.py   Hàng đợi mô phỏng đa luồng, giới hạn đồng thời, thử lại có lùi thời gian
  pipeline/scorer.py   Ngưỡng chấm điểm Sharpe, Fitness, Turnover, Drawdown, Margin
  pipeline/correlation.py  Kiểm tra tự tương quan và tương quan với alpha đã nộp
  web/app.py           FastAPI phục vụ bảng theo dõi
  cli.py               Điểm vào dòng lệnh
```

Luồng dữ liệu chạy một chiều:

1. Bộ sinh tạo danh sách biểu thức và ghi vào bảng `alphas` với trạng thái `PENDING`.
2. Bộ chạy lấy các bản ghi `PENDING`, gửi mô phỏng lên BRAIN, cập nhật `RUNNING` rồi `SIMULATED` hoặc `FAILED`.
3. Bộ chấm điểm đọc chỉ số, đối chiếu ngưỡng, gán trạng thái `PASSED` hoặc `REJECTED` kèm lý do.
4. Bộ kiểm tra tương quan chỉ chạy trên nhóm `PASSED` vì mỗi lần gọi tốn tài nguyên máy chủ.
5. Giao diện web đọc trực tiếp SQLite, không giữ trạng thái riêng, nên có thể mở nhiều tab hoặc tắt đi bật lại tùy ý.

## 2. Research Layer

Alpha Forge đang được mở rộng từ một pipeline tạo và kiểm tra alpha thành một hệ thống ghi nhớ quá trình nghiên cứu.

Giai đoạn một bổ sung bốn khái niệm:

```text
Research Project
      ↓
Hypothesis
      ↓
Experiment
      ↓
Experiment Variant
```

Đồng thời có bảng `alpha_lineage` để ghi quan hệ cha con giữa các alpha.

Các mô hình nằm trong `research_models.py` và kho SQLite độc lập nằm trong `research_store.py`. Thiết kế này cố ý chưa thay đổi pipeline BRAIN hiện tại, để lớp nghiên cứu có thể được kiểm thử độc lập trước khi tích hợp vào kho alpha chính.

Ví dụ:

```bash
python research_cli.py init --db data/research.db
python research_cli.py project-create --db data/research.db --name "Momentum Volume" --family "Momentum"
python research_cli.py project-list --db data/research.db
python research_cli.py hypothesis-create --db data/research.db --research-id 1 --statement "Momentum mạnh hơn khi volume xác nhận biến động giá."
python research_cli.py experiment-create --db data/research.db --hypothesis-id 1 --name "Lookback test" --variable "lookback"
```

Lớp này chưa gọi BRAIN và chưa sinh expression. Mục tiêu là tạo được trí nhớ nghiên cứu trước, sau đó nối `Experiment` vào `GeneratorEngine`.

## 3. Cài đặt

```bash
git clone <địa chỉ kho>
cd alpha-forge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Điền tài khoản BRAIN vào tệp `.env`. Tệp này đã nằm trong `.gitignore`.

## 4. Sử dụng

Kiểm tra kết nối và hạn mức tài khoản:

```bash
python -m alphaforge.cli auth
```

Tải danh mục trường dữ liệu về máy để bộ sinh dùng lại mà không gọi mạng nhiều lần:

```bash
python -m alphaforge.cli fields --region USA --universe TOP3000 --delay 1 --dataset fundamental6
```

Sinh biểu thức:

```bash
python -m alphaforge.cli generate --strategy template --limit 300 --tag lot1
python -m alphaforge.cli generate --strategy pairwise --limit 500 --tag lot2
```

Chạy mô phỏng:

```bash
python -m alphaforge.cli run --concurrency 3 --limit 300
```

Chấm điểm và lọc:

```bash
python -m alphaforge.cli score
python -m alphaforge.cli correlate --max-self 0.7
```

Xuất danh sách đề xuất nộp:

```bash
python -m alphaforge.cli report --top 50 --out top_alphas.csv
```

Mở bảng theo dõi:

```bash
python -m alphaforge.cli web --port 8000
```

## 5. Nguyên tắc vận hành

Tài khoản BRAIN có hạn mức mô phỏng đồng thời theo cấp bậc người dùng. Đặt `concurrency` vượt hạn mức sẽ khiến máy chủ trả về lỗi 429 và toàn bộ hàng đợi bị chậm lại. Giá trị mặc định là 3 và nên tăng dần sau khi quan sát thực tế.

Phiên đăng nhập BRAIN hết hạn sau một khoảng thời gian. Lõi kết nối tự động đăng nhập lại khi gặp mã 401 và thử lại yêu cầu đúng một lần để tránh vòng lặp vô hạn.

Bộ chấm điểm không thay thế phán đoán của người nghiên cứu. Ngưỡng mặc định trong `config/settings.yaml` dựa trên tiêu chí nộp thông thường của BRAIN, tuy nhiên tiêu chí này thay đổi theo từng đợt và theo từng khu vực, nên cần đối chiếu lại với tài liệu chính thức trước mỗi đợt nộp.

Tự tương quan cao là nguyên nhân bị loại phổ biến hơn cả chỉ số kém. Nên chạy bước `correlate` trước khi cân nhắc nộp bất kỳ biểu thức nào.

## 6. Giới hạn đã biết

Dự án không đóng gói bộ mô phỏng riêng. Mọi chỉ số đều lấy từ máy chủ BRAIN, do đó không thể chạy ngoại tuyến.

Bộ sinh dựa trên mẫu và tổ hợp toán tử, không dựa trên mô hình ngôn ngữ. Cách này cho kết quả ổn định và tái lập được, đổi lại độ đa dạng thấp hơn so với hướng dùng mô hình sinh. Điểm mở rộng nằm ở `alphaforge/generator/engine.py`.

Cấu trúc điểm cuối của BRAIN do bên thứ ba vận hành và có thể thay đổi mà không báo trước. Khi một lệnh trả về lỗi phân tích dữ liệu, cần kiểm tra lại tài liệu API trước khi sửa mã.

## 7. Kiểm thử

Giai đoạn một có kiểm thử độc lập cho vòng đời Research Project → Hypothesis → Experiment → Variant và alpha lineage:

```bash
pytest test_research.py
```

## 8. Giấy phép

MIT. Xem tệp LICENSE.
