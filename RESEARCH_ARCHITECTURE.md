# Kiến trúc Research Layer

## Mục tiêu

Mở rộng Alpha Forge từ một pipeline tạo và kiểm tra alpha thành hệ thống ghi nhớ quá trình nghiên cứu, nhưng không làm thay đổi BRAIN client và simulation pipeline trong giai đoạn đầu.

## Nguyên tắc

```text
Research
  → Hypothesis
  → Experiment
  → Variant
  → Alpha
  → Simulation
  → Validation
```

Research layer chịu trách nhiệm về ngữ cảnh và lịch sử nghiên cứu. Simulation layer chịu trách nhiệm thực thi với BRAIN. Hai lớp không được phụ thuộc ngược vào nhau.

## Thành phần giai đoạn một

`research_models.py`

Chứa các mô hình ResearchProject, Hypothesis, Experiment, ExperimentVariant và AlphaLineage.

`research_store.py`

Kho SQLite độc lập. Tự tạo schema khi khởi tạo. Có khóa ngoại và chỉ mục cho các quan hệ nghiên cứu.

`research_cli.py`

CLI tạm thời để kiểm thử lớp nghiên cứu độc lập. Sau khi schema ổn định, các lệnh sẽ được hợp nhất vào CLI chính.

`test_research.py`

Kiểm thử vòng đời Research Project → Hypothesis → Experiment → Variant và quan hệ cha con alpha.

## Giai đoạn tiếp theo

1. Hợp nhất schema research vào kho alpha chính.
2. Thêm `research_id`, `hypothesis_id`, `experiment_id` và `parent_alpha_id` vào alpha record.
3. Nối Experiment với GenerationRequest.
4. Đưa metadata của experiment vào mỗi alpha candidate.
5. Nâng runner để lưu trạng thái theo experiment.
6. Thêm robustness validation.
7. Mở rộng web dashboard.

## Không làm ở giai đoạn một

Không thêm Claude API.

Không thêm Ollama.

Không thay thế BrainClient.

Không thay thế SimulationRunner.

Không chuyển SQLite sang PostgreSQL.

Không chuyển FastAPI sang Streamlit.

Mục tiêu là kiểm soát phạm vi thay đổi và bảo toàn pipeline BRAIN đang hoạt động.
