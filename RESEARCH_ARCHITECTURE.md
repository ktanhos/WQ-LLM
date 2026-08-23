# Kiến trúc Alpha Research Hub

## Alpha Research Hub không phải Alpha Generator

Một bộ sinh alpha tối ưu số lượng: sinh càng nhiều biểu thức càng tốt, mô phỏng
tất cả, giữ lại cái nào Sharpe cao. Cách đó hỏng ở quy mô lớn vì hai lý do.

Thứ nhất, nó không nhớ gì. Sau năm nghìn alpha, nó vẫn sinh lại chính những họ
cấu trúc đã cho kết quả kém, chỉ khác cách viết.

Thứ hai, nó không tạo ra thông tin. Chọn alpha tốt nhất từ hàng nghìn phép thử
trên cùng tập dữ liệu là khai thác dữ liệu. Điểm cao trong mẫu không nói gì về
ngoài mẫu, và không ai biết vì sao alpha đó tốt.

Hệ thống này gồm bảy phần, và bộ sinh chỉ là một trong bảy:

```text
Research Memory  +  Experiment Engine  +  Alpha Generator  +  Validation
                 +  Simulation  +  Analytics  +  Research Feedback
```

Mục tiêu không phải "sinh nhiều alpha" mà là **mỗi lượt mô phỏng phải tạo thêm
thông tin nghiên cứu**. Một alpha thất bại vẫn có giá trị nếu biết nó thất bại
trong điều kiện nào.

## Vòng đời đầy đủ

```text
Research Project
      │
      ▼
Research History ────► Research Gap ────► Research Priority
   (đã thử gì)          (thiếu chỗ nào)     (nên thử chỗ nào trước)
      │                                            │
      │                                            ▼
      │                                      Hypothesis
      │                                            │
      │                                            ▼
      │                                      Experiment
      │                                   (một thay đổi thiết kế)
      │                                            │
      │                                            ▼
      │                                    Generation Plan
      │                                            │
      ▼                                            ▼
Research Memory ──────► Alpha Generation ──► Alpha Validation
   (trọng số)                                      │
                                                   ▼
                                              Simulation
                                                   │
                                                   ▼
                                                Scoring
                                                   │
                                                   ▼
                                              Robustness
                                                   │
                                                   ▼
                                    Structural Similarity (cục bộ)
                                                   │
                                                   ▼
                                     Self / Product Correlation
                                                   │
                                                   ▼
                                               CANDIDATE
                                                   │
                                                   ▼
                                            HUMAN DECISION
                                                   │
                                                   ▼
                                              Submission
                                                   │
                                                   ▼
                                          Historical Memory
                                                   │
                                                   └──► Research Gap mới
```

Vòng khép kín. Alpha đã nộp quay lại làm dữ liệu cho lượt nghiên cứu sau.

**Bước nộp luôn do người thực hiện.** Không mã nào trong kho gọi điểm cuối nộp
alpha của nền tảng. Lệnh `alpha mark-submitted` chỉ ghi chép việc người dùng
nói rằng họ đã tự nộp.

## Bảy thành phần

### 1. Research Memory — `research/memory.py`

Gộp mọi bằng chứng nghiên cứu về một dạng bản ghi thống nhất, phân biệt năm
nguồn:

| Nguồn | Nghĩa |
| --- | --- |
| `historical` | nhập từ lịch sử nộp trên nền tảng |
| `submitted` | đã nộp, dù đến từ nguồn nào |
| `experiment` | sinh trong khuôn khổ một thí nghiệm |
| `simulation` | đã mô phỏng, không gắn thí nghiệm nào |
| `current` | đang trong hàng đợi, chưa có kết quả |

Trả lời được: trường nào, toán tử nào, họ nào, cấu trúc nào, cửa sổ nào, khu
vực nào, universe nào, cách trung tính hóa nào đã được thử; alpha nào đạt, alpha
nào thất bại, alpha nào bị loại **riêng vì tương quan**, alpha nào đã nộp.

Phân biệt "bị loại vì tương quan" với "bị loại vì chỉ số kém" là quan trọng: cái
đầu nghĩa là ý tưởng đúng nhưng đã có người khai thác, cái sau nghĩa là ý tưởng
chưa hiệu quả. Hai kết luận nghiên cứu hoàn toàn khác nhau.

### 2. Research Gap — `research/gap.py`

Tìm chỗ thiếu bằng chứng trên tám chiều: trường dữ liệu, toán tử, cửa sổ nhìn
lại, họ cấu trúc, thiết lập, khu vực, universe, trung tính hóa.

Phân biệt hai loại thiếu hụt:

| Loại | Ví dụ |
| --- | --- |
| thiếu theo lượng | trường B mới thử 5 alpha, trường A đã thử 200 |
| thiếu theo phủ | họ C có 100 alpha nhưng **tất cả đều delay 1** |

Loại thứ hai nguy hiểm hơn vì nhìn số lượng sẽ tưởng đã khảo sát kỹ.

Giá trị **chưa xuất hiện lần nào** không thể phát hiện bằng cách đếm dữ liệu đã
có, nên `find()` nhận thêm danh mục giá trị hợp lệ để so.

### 3. Research Priority — `research/priority.py`

Không dùng quy tắc "ít alpha thì ưu tiên cao". Một vùng chưa ai thử có thể chưa
ai thử vì nó vô nghĩa.

```text
priority = underexplored^w1 × performance^w2 × diversity^w3 × recency^w4
```

Dùng tích chứ không dùng tổng: một thành phần bằng 0 thì cả điểm bằng 0.

Mọi điểm kèm danh sách lý do:

```text
Research Priority = 0.46 (field: close)
Cỡ mẫu 3, độ tin cậy 0.07
Thành phần: underexplored=0.925, performance=0.5, diversity=1.0, recency=1.0
Lý do:
  - Còn ít quan sát, mới 3 alpha.
  - Chưa đủ dữ liệu hiệu năng, tạm coi là trung tính.
  - Cỡ mẫu 3 còn quá nhỏ, điểm này là phỏng đoán chứ chưa phải kết luận.
```

Trọng số cấu hình được qua `PriorityWeights`. Đặt một trọng số về 0 thì thành
phần đó không còn ảnh hưởng.

### 4. Experiment Engine — `research/experiment.py`

**Một thí nghiệm = một thay đổi thiết kế chính.** Nếu đổi đồng thời cửa sổ,
trường dữ liệu và cách trung tính hóa rồi thấy kết quả tốt lên, không thể quy
kết quả đó cho yếu tố nào.

Engine chặn việc đổi nhiều biến bằng cách đối chiếu vân tay của biến thể với
biểu thức gốc. Muốn đổi nhiều biến vẫn được, nhưng phải khai báo
`allow_multiple_changes=True` — khai báo tường minh buộc người thiết kế ý thức
rằng kết quả sẽ khó quy kết.

Biến khảo sát nằm ở biểu thức (cửa sổ, trường, toán tử) hoặc ở thiết lập mô
phỏng (trung tính hóa, decay, truncation, universe, khu vực, delay). Biến thuộc
thiết lập thì biểu thức giữ nguyên hoàn toàn.

### 5. Generation Plan — `research/plan.py`

Bộ sinh không nhận tham số rời rạc từ dòng lệnh nữa. Nó nhận một kế hoạch đã
qua kiểm tra, ghi đủ ngữ cảnh: dự án, giả thuyết, thí nghiệm, trường dữ liệu,
cửa sổ, mẫu, ràng buộc, hạt giống.

Bốn chế độ:

| Chế độ | Dùng khi |
| --- | --- |
| `template` | điền trường vào mẫu có sẵn |
| `pairwise` | ghép cặp hai trường |
| `mutate` | biến đổi biểu thức gốc |
| `direct` | biểu thức đã xác định, không sinh gì thêm |

Chế độ `direct` dành cho biến thể của thí nghiệm. Cho chúng đi qua bộ sinh sẽ
bọc thêm toán tử và phá vỡ đúng cái mà thí nghiệm muốn cô lập.

### 6. Alpha Validation — `pipeline/validation.py`

Chặn biểu thức hỏng **trước khi** tốn hạn mức mô phỏng: cú pháp, toán tử tồn
tại, trường dữ liệu tồn tại, độ dài, số toán tử, số trường, độ sâu, trùng lặp,
ràng buộc nghiên cứu.

Danh mục toán tử và trường là **tùy chọn**. Chưa tải về thì bỏ qua bước đối
chiếu thay vì đoán bừa, vì nền tảng liên tục bổ sung toán tử mới.

Biểu thức không hợp lệ được lưu với trạng thái `INVALID` kèm lý do, chứ không
vứt đi im lặng: biết nó sai ở đâu cũng là thông tin, và giữ lại giúp không sinh
lại lần sau.

### 7. Bậc thang thẩm định — `pipeline/evaluation.py`

Mỗi bước tốn tài nguyên hơn bước trước, nên alpha phải vượt bước rẻ mới được
đưa lên bước đắt:

| Bước | Chi phí |
| --- | --- |
| 1. chấm điểm | đọc chỉ số đã lưu |
| 2. độ bền | đọc chỉ số đã lưu |
| 3. tương đồng cấu trúc | so vân tay trong kho |
| 4. tự tương quan | gọi máy chủ |
| 5. tương quan sản phẩm | gọi máy chủ, tốn nhất |

Bước ba đáng giá nhất: nó loại alpha trùng ý tưởng với thứ đã có **trước khi**
tốn một lượt gọi tương quan. Hai biểu thức cùng họ cấu trúc gần như chắc chắn
tương quan cao, và điều đó biết được tại chỗ.

## Hai thang trạng thái

Tách làm hai vì chúng trả lời hai câu hỏi khác nhau.

`Status` — bản ghi đang ở đâu trong hàng đợi:

```text
PENDING → RUNNING → SIMULATED → PASSED ─┐
                              → REJECTED │
                              → FAILED   │
INVALID (chưa từng vào hàng đợi)         │
                                         ▼
                              CANDIDATE → HUMAN_REVIEW → SUBMITTED
```

`EvaluationStatus` — đã vượt bao nhiêu bước thẩm định:

```text
SCORED → ROBUST → CORRELATION_PASS → CANDIDATE
      ↘ ROBUST_FAILED   ↘ CORRELATION_FAILED
```

Một alpha có thể `PASSED` ở hàng đợi nhưng mới chỉ `SCORED` ở thang thẩm định.

## Một kho dữ liệu duy nhất

| Bảng | Nội dung |
| --- | --- |
| `runs` | mỗi lô sinh |
| `alphas` | biểu thức, trạng thái, chỉ số, vân tay, phả hệ, độ bền |
| `historical_alphas` | alpha đã nộp nhập từ nền tảng |
| `research_projects` | dự án nghiên cứu |
| `hypotheses` | giả thuyết thuộc dự án |
| `experiments` | thí nghiệm, kèm thiết lập mô phỏng |
| `experiment_variants` | biến thể trong một thí nghiệm |
| `generation_plans` | kế hoạch sinh đã chạy |
| `alpha_lineage` | quan hệ cha con và nguồn gốc |
| `correlation_results` | từng lượt kiểm tra tương quan, kèm ngưỡng đã dùng |
| `data_fields` | danh mục trường dữ liệu |
| `events` | nhật ký |

Không tạo kho thứ hai. Câu hỏi nghiên cứu quan trọng nhất — "cấu trúc này đã
thử chưa" — luôn cần nối alpha đang sinh với alpha đã nộp và với thí nghiệm đã
thiết kế. Ba tệp riêng buộc phải nối thủ công trong Python.

Kho tạo bởi phiên bản trước được di trú tự động khi mở.

## Khả năng tái lập

Mỗi alpha lưu đủ để dựng lại: biểu thức, **toàn bộ thiết lập mô phỏng** (không
tham chiếu cấu hình hiện tại), chiến lược sinh, **hạt giống**, mã kế hoạch, mã
thí nghiệm, mã biến thể, alpha cha, alpha nguồn.

Khóa khử trùng lặp gồm cả biểu thức lẫn thiết lập, nên cùng một biểu thức chạy ở
hai khu vực là hai thí nghiệm riêng biệt.

## Kết luận nghiên cứu

Báo cáo thí nghiệm phân biệt bốn mức:

| Kết luận | Khi nào |
| --- | --- |
| `evidence_insufficient` | cỡ mẫu chưa đủ, **chưa kết luận gì** |
| `inconclusive` | đủ mẫu nhưng không thấy hiệu ứng rõ |
| `supports_hypothesis` | hiệu ứng đúng chiều kỳ vọng |
| `contradicts_hypothesis` | hiệu ứng ngược chiều kỳ vọng |

"Bằng chứng chưa đủ" là kết luận hợp lệ và thường gặp nhất. Nó không phải thất
bại của thí nghiệm; nó cho biết cần chạy thêm bao nhiêu nữa.

## Những điều hệ thống cố ý không làm

Không tự nộp alpha. Không tự chạy hàng loạt mô phỏng — mọi lệnh đều có giới hạn
số lượng. Không để mô hình ngôn ngữ điều khiển hệ thống; nó chỉ sinh văn bản cho
người đọc. Không gọi BRAIN từ bảng theo dõi.

Không để Research Memory thành bộ lọc cứng. Trọng số **không bao giờ bằng
không**: một họ từng cho kết quả kém vẫn có thể tốt trở lại khi thị trường đổi
trạng thái, nên khóa cứng không gian tìm kiếm theo quá khứ là sai. Research
Memory chỉ định hướng khám phá.
