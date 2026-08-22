# Alpha Forge

Bộ khung Python cho toàn bộ vòng đời nghiên cứu alpha trên nền tảng WorldQuant BRAIN: ghi nhớ những gì đã nghiên cứu, sinh biểu thức mới dựa trên trí nhớ đó, gửi mô phỏng, chấm điểm, lọc tương quan và theo dõi tiến độ qua giao diện web.

Điểm khác biệt so với một bộ sinh biểu thức thông thường là **trí nhớ nghiên cứu**. Hệ thống nhập lịch sử alpha đã nộp từ BRAIN, phân tích cấu trúc của chúng, và dùng kết quả đó để tránh lặp lại những hướng đã bão hòa.

## 1. Vòng nghiên cứu

```text
Historical BRAIN
      │  history scan
      ▼
Research Memory ──────────► Research Gap
      │                          │
      │                          ▼
      │                     Hypothesis
      │                          │
      │                          ▼
      │                     Experiment
      │                          │
      ▼                          ▼
  Generator ◄──── trọng số theo họ cấu trúc
      │
      ▼
  Simulation ──► Score ──► Correlation ──► Candidate
                                               │
                                               ▼
                                    Submission (người quyết định)
                                               │
                                               ▼
                                       Historical BRAIN
```

Vòng lặp khép kín: alpha đã nộp quay lại làm dữ liệu cho trí nhớ nghiên cứu ở lượt sau. Bước nộp alpha **luôn do người thực hiện**; hệ thống không bao giờ tự nộp, kể cả khi một alpha vượt mọi ngưỡng.

## 2. Kiến trúc

```text
alphaforge/
  config.py              Nạp cấu hình từ YAML và biến môi trường
  cli.py                 Điểm vào dòng lệnh duy nhất
  brain/
    client.py            Lõi kết nối BRAIN: xác thực, mô phỏng, chỉ số, trường dữ liệu
    errors.py            Phân loại lỗi để lớp trên quyết định thử lại
  storage/
    db.py                Kho SQLite hợp nhất, một tệp cho toàn hệ thống
  generator/
    engine.py            Sinh biểu thức theo mẫu, có nhận trí nhớ nghiên cứu
    templates.py         Danh mục mẫu biểu thức
  pipeline/
    runner.py            Hàng đợi mô phỏng đa luồng, tự giảm tốc khi bị hạn mức
    scorer.py            Ngưỡng Sharpe, Fitness, Turnover, Drawdown, Margin
    correlation.py       Tự tương quan và tương quan với danh mục sản phẩm
  history/
    scanner.py           Nhập lịch sử alpha đã nộp từ BRAIN
    fingerprint.py       Vân tay cấu trúc ba mức và phát hiện trùng lặp
    analyzer.py          Thống kê theo trung vị, phân vị và cỡ mẫu
    report.py            Báo cáo nghiên cứu tổng hợp
  research/
    models.py            ResearchProject, Hypothesis, Experiment, Variant, Lineage
    store.py             Kho cho lớp nghiên cứu
    memory.py            Trí nhớ nghiên cứu lái bộ sinh
  llm/                   Mô hình ngôn ngữ tùy chọn: Claude, Ollama, hoặc không dùng
  web/app.py             FastAPI phục vụ bảng theo dõi, chỉ đọc kho
config/settings.yaml     Cấu hình mặc định
tests/                   Kiểm thử, toàn bộ chạy ngoại tuyến
```

### Một kho dữ liệu duy nhất

Toàn bộ hệ thống dùng chung một tệp SQLite. Bảng chính:

| Bảng | Nội dung |
| --- | --- |
| `runs` | Mỗi lô sinh biểu thức |
| `alphas` | Biểu thức, trạng thái, chỉ số, vân tay, phả hệ |
| `historical_alphas` | Alpha đã nộp nhập về từ BRAIN |
| `research_projects` | Dự án nghiên cứu |
| `hypotheses` | Giả thuyết thuộc dự án |
| `experiments` | Thí nghiệm thuộc giả thuyết, kèm thiết lập mô phỏng |
| `experiment_variants` | Biến thể trong một thí nghiệm |
| `alpha_lineage` | Quan hệ cha con và nguồn gốc từng alpha |
| `data_fields` | Danh mục trường dữ liệu tải về |
| `events` | Nhật ký sự kiện |

Lý do hợp nhất thay vì tách nhiều tệp: câu hỏi nghiên cứu quan trọng nhất là "cấu trúc này đã thử chưa", và nó luôn cần nối alpha đang sinh với alpha đã nộp và với thí nghiệm đã thiết kế. Ba tệp riêng buộc phải nối thủ công trong Python, mất khả năng JOIN và mất tính nguyên tử khi một thao tác chạm vào hai lớp. SQLite khóa theo tệp nên nhiều tệp cũng không giảm tranh chấp.

Kho tạo bởi phiên bản trước được **di trú tự động**: cột còn thiếu sẽ được thêm khi mở kho, dữ liệu cũ giữ nguyên.

## 3. Vân tay cấu trúc

Băm toàn bộ chuỗi biểu thức không trả lời được câu hỏi nghiên cứu. Mô đun `history/fingerprint.py` tạo ba mức lồng nhau:

| Mức | Trừu tượng hóa | `ts_mean(returns,20)` với `ts_mean(returns,60)` | với `ts_mean(volume,20)` |
| --- | --- | --- | --- |
| `exact` | chỉ chuẩn hóa khoảng trắng và chữ hoa thường | khác nhau | khác nhau |
| `family` | hằng số thành `#`, giữ trường dữ liệu | **giống nhau** | khác nhau |
| `template` | hằng số và trường dữ liệu đều trừu tượng | giống nhau | **giống nhau** |

Nhờ vậy hệ thống trả lời được: cấu trúc này đã nghiên cứu chưa, có bao nhiêu alpha tương tự, đã thử cửa sổ nào, cửa sổ nào tốt nhất, trường dữ liệu nào đã khai thác, tổ hợp toán tử nào đang bị lặp nhiều.

Toán tử được nhận diện theo cú pháp (định danh đứng ngay trước dấu mở ngoặc) chứ không theo danh sách cố định, nên không lỗi thời khi BRAIN bổ sung toán tử mới. Nhóm phân loại như `subindustry` và tên đối số theo khóa như `std` không bị nhầm thành trường dữ liệu.

## 4. Cài đặt

```bash
git clone <địa chỉ kho>
cd alpha-forge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Điền tài khoản BRAIN vào `.env`. Tệp này đã nằm trong `.gitignore`.

## 5. Sử dụng

### Quy trình cơ bản

```bash
python -m alphaforge.cli auth
python -m alphaforge.cli fields --region USA --universe TOP3000 --delay 1 --dataset fundamental6
python -m alphaforge.cli generate --strategy template --limit 300 --tag lot1
python -m alphaforge.cli run --concurrency 3 --limit 300
python -m alphaforge.cli score
python -m alphaforge.cli correlate --max-self 0.7
python -m alphaforge.cli report --top 50 --out top_alphas.csv
```

### Trí nhớ nghiên cứu

```bash
# Nhập lịch sử alpha đã nộp. Nên chạy trước khi sinh lô mới.
python -m alphaforge.cli history scan --start-date 2026-01-01 --end-date 2026-08-22

# Báo cáo: thống kê, phân phối, cấu trúc lặp lại, khoảng trống
python -m alphaforge.cli history report
python -m alphaforge.cli history report --full --out bao_cao.json

# Khoảng trống đáng khảo sát tiếp
python -m alphaforge.cli history gaps --limit 20

# Biểu thức này đã được nghiên cứu chưa
python -m alphaforge.cli history check "rank(ts_mean(returns, 20))" --duplicates
```

Sau khi có lịch sử, lệnh `generate` tự động hạ ưu tiên những họ cấu trúc đã thử nhiều mà kết quả kém, và bỏ qua biểu thức đã tồn tại. Dùng `--no-memory` để quay lại hành vi phân phối đều.

### Lớp nghiên cứu

```bash
python -m alphaforge.cli research project create --name "Momentum Volume" --family Momentum
python -m alphaforge.cli research project list
python -m alphaforge.cli research hypothesis create --research-id 1 --statement "Volume xác nhận momentum"
python -m alphaforge.cli research experiment create --hypothesis-id 1 --name "Lookback test" --variable lookback
python -m alphaforge.cli research lineage ALPHA-123
```

### Bảng theo dõi

```bash
python -m alphaforge.cli web --port 8000
```

Bảng theo dõi chỉ đọc SQLite, **không bao giờ gọi BRAIN**. Có thể mở nhiều tab, tắt bật tùy ý mà không ảnh hưởng tiến trình đang chạy. Ngoài hàng đợi mô phỏng, bảng còn hiển thị: trí nhớ nghiên cứu, họ cấu trúc tốt và kém, khoảng trống nghiên cứu, lịch sử nộp, dự án nghiên cứu và tương quan.

### Mô hình ngôn ngữ (tùy chọn)

```bash
pip install anthropic
export ANTHROPIC_API_KEY=...
export ALPHAFORGE_LLM_PROVIDER=claude   # hoặc ollama, hoặc none

python -m alphaforge.cli history explain --task summary
python -m alphaforge.cli history explain --task hypotheses
```

Mô hình ngôn ngữ chỉ sinh văn bản để người đọc: tóm tắt nghiên cứu, đề xuất giả thuyết, phân tích khoảng trống, giải thích biểu thức. Nó **không** được nộp alpha, sửa cấu hình hay gọi BRAIN. Bộ sinh tất định hoạt động đầy đủ khi không có mô hình ngôn ngữ; khóa API chỉ đọc từ biến môi trường, không bao giờ từ tệp cấu hình.

## 6. Nguyên tắc vận hành

Tài khoản BRAIN có hạn mức mô phỏng đồng thời theo cấp bậc người dùng. Giá trị `concurrency` là **trần**, không phải mức cố định: khi máy chủ trả về 429, hàng đợi tự tạm dừng và giảm một nửa mức đồng thời, rồi nới dần trở lại sau một chuỗi lượt thành công.

Phiên đăng nhập hết hạn sau một khoảng thời gian. Lõi kết nối tự đăng nhập lại khi gặp 401 và thử lại đúng một lần để tránh vòng lặp vô hạn.

Bộ chấm điểm không thay thế phán đoán của người nghiên cứu. Ngưỡng mặc định dựa trên tiêu chí nộp thông thường của BRAIN, nhưng tiêu chí thay đổi theo từng đợt và từng khu vực, nên cần đối chiếu tài liệu chính thức trước mỗi đợt nộp.

Tự tương quan cao là nguyên nhân bị loại phổ biến hơn cả chỉ số kém. Bước `correlate` chỉ chạy trên nhóm đã đạt ngưỡng vì mỗi lần gọi tốn tài nguyên máy chủ.

Thống kê nghiên cứu dùng trung vị và phân vị thay vì trung bình, và luôn kèm cỡ mẫu. Một họ cấu trúc có trung vị Sharpe cao trên hai alpha không nói lên điều gì; cùng con số đó trên tám mươi alpha lại là một phát hiện.

Trí nhớ nghiên cứu giữ cả alpha thất bại. Nếu một họ có một trăm alpha mà chỉ hai alpha đạt thì tỷ lệ đạt phải là hai phần trăm. Chỉ lưu alpha tốt sẽ tạo thiên lệch sống sót và làm mọi họ cấu trúc trông như nhau.

## 7. Giới hạn đã biết

Dự án không đóng gói bộ mô phỏng riêng. Mọi chỉ số đều lấy từ máy chủ BRAIN, do đó không thể chạy mô phỏng ngoại tuyến. Toàn bộ kiểm thử thì chạy ngoại tuyến hoàn toàn nhờ bản giả lập máy chủ.

Bộ sinh dựa trên mẫu và tổ hợp toán tử, không dựa trên mô hình ngôn ngữ. Cách này cho kết quả ổn định và tái lập được, đổi lại độ đa dạng thấp hơn.

Chọn ra biểu thức tốt nhất từ hàng nghìn phép thử trên cùng một tập dữ liệu là hình thức khai thác dữ liệu. Điểm cao trong mẫu không bảo đảm hiệu năng ngoài mẫu. Số lượng phép thử nên được ghi nhận khi báo cáo kết quả; trí nhớ nghiên cứu chính là nơi lưu con số đó.

Cấu trúc điểm cuối của BRAIN do bên thứ ba vận hành và có thể thay đổi mà không báo trước. Khi một lệnh trả về lỗi phân tích dữ liệu, cần kiểm tra lại tài liệu API trước khi sửa mã.

SQLite khóa toàn bộ tệp khi ghi nên không phù hợp cho nhiều tiến trình trên các máy khác nhau. Muốn chạy phân tán thì thay `storage/db.py` bằng lớp tương đương dùng PostgreSQL.

## 8. Kiểm thử

```bash
pytest -q
python -m compileall -q alphaforge tests
```

Không kiểm thử nào gọi máy chủ BRAIN thật. Máy chủ được thay bằng bản giả lập trong `tests/conftest.py`.

## 9. Giấy phép

MIT. Xem tệp LICENSE.
