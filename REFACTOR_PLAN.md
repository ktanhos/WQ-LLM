# WQ-LLM Refactor Plan

## Mục tiêu

Tinh gọn WQ-LLM trước khi chạy thử Alpha thực tế. Giữ phần nghiên cứu có giá trị, thay lớp kết nối và metadata bằng một kiến trúc đơn giản, kiểm thử độc lập được và có bộ nhớ đệm cục bộ.

## Kiến trúc đích

```text
alphaforge/
  brain/
    client.py          # HTTP, xác thực, request, mô phỏng
    metadata.py        # tải operators, datasets, fields
    cache.py           # cache và metadata manifest
    errors.py
  research/
    hypothesis.py
    template.py
    generation.py
  evaluation/
    scorer.py
    correlation.py
    robustness.py
  simulation/
    runner.py
  storage/
    db.py
  cli.py
```

## Quyết định giữ

- `alphaforge/brain/client.py` nhưng tách metadata ra khỏi client.
- `alphaforge/storage` và SQLite.
- `pipeline/scorer.py`.
- `pipeline/correlation.py`.
- `pipeline/robustness.py`.
- `generator` và `research` nhưng không mở rộng không gian tìm kiếm lớn.
- `llm` chỉ là lớp hỗ trợ nghiên cứu, không nằm trên đường chạy bắt buộc.

## Quyết định thay đổi

### Brain client

`BrainClient` chỉ phụ trách:

- authenticate
- request
- submit_simulation
- wait_for_simulation
- get_alpha
- correlations

Không chứa logic tải toàn bộ metadata.

### Metadata

Tạo `brain/metadata.py` với:

- `get_operators`
- `get_data_fields`
- `sync_operators`
- `sync_dataset_fields`
- `sync_all_enabled_datasets`

### Cache

Lưu theo cấu trúc:

```text
data/worldquant/
  operators.json
  datasets.json
  fields/
    pv1.json
    fundamental6.json
    analyst4.json
  manifest.json
```

`manifest.json` lưu thời điểm đồng bộ, tham số region, universe, delay và số lượng bản ghi.

Không tải lại nếu cache còn hợp lệ, trừ khi người dùng yêu cầu `--refresh`.

### Phân trang

Không giả định giới hạn máy chủ là 50 hoặc 200 trong mã nguồn. Dùng `page_size` cấu hình, mặc định 200. Nếu máy chủ trả lỗi do giới hạn trang, hạ dần 100 rồi 50 và ghi kích thước hợp lệ vào manifest cho lần sau.

### Dataset

Danh sách dataset không hard code trong Python. Đưa vào `config/settings.yaml`.

## Những phần chưa đưa vào giai đoạn 1

- LLM tự sửa Alpha rồi tự gửi lại.
- Sinh hàng chục nghìn Alpha bằng tích Descartes.
- Multi agent.
- Phân cụm semantic fields bằng mô hình ngôn ngữ.
- Web UI như một phụ thuộc bắt buộc.

## Thứ tự thực hiện

1. Tách metadata khỏi `BrainClient`.
2. Tạo cache metadata.
3. Thêm lệnh CLI `metadata sync`, `metadata status`, `metadata refresh`.
4. Kiểm thử đăng nhập độc lập.
5. Kiểm thử operators độc lập.
6. Kiểm thử fields theo từng dataset.
7. Kiểm thử cache không gọi API lần hai.
8. Kiểm thử một simulation duy nhất.
9. Sau khi các bước trên ổn định mới nối lại generation, scoring, correlation và robustness.

## Tiêu chí sẵn sàng chạy thử

Một môi trường sạch phải làm được theo thứ tự:

```text
authenticate
metadata sync --datasets pv1
metadata sync --datasets fundamental6
metadata status
simulate --expression <một biểu thức>
```

Mỗi bước phải chạy độc lập và có test riêng.

## Nguyên tắc nghiên cứu

Luồng nghiên cứu ban đầu:

```text
Hypothesis
  -> Template
  -> 10 đến 20 variants có chủ đích
  -> Syntax validation
  -> Simulation
  -> Scoring
  -> Correlation
  -> Robustness
```

Không chạy tự động quy mô lớn trong giai đoạn xác nhận kiến trúc.
