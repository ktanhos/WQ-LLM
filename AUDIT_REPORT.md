# Báo cáo audit mã nguồn

**Phạm vi:** chỉ mã nguồn và môi trường cục bộ.
**Ngày:** 2026-08-23
**Nhánh:** `claude/repo-permissions-check-fnkojz`
**Kết luận:** `CODE VERIFIED` — **không phải** `BRAIN VERIFIED`.

Đợt audit này không dùng thông tin đăng nhập, không gọi WorldQuant BRAIN, không
chạy mô phỏng thật và không nộp alpha. Mọi tương tác với máy chủ đều qua bản
giả lập. Vì vậy phần tích hợp BRAIN được xếp vào mục `NOT TESTED`, không phải
`PASS`.

---

## 1. Kết quả cổng kiểm tra

| Cổng | Lệnh | Kết quả |
| --- | --- | --- |
| Kiểm thử | `pytest -q` | **PASS** — 290 passed, 27.87s |
| Biên dịch | `python -m compileall .` | **PASS** — exit 0 |
| Nhập package | duyệt `pkgutil.walk_packages` | **PASS** — 30/30 module, 0 lỗi |
| Điểm vào CLI | `alphaforge --help` | **PASS** — 13 nhóm lệnh |
| Phụ thuộc vòng | phân tích AST | **PASS** — 0 vòng |

Số kiểm thử tăng từ 214 lên **290** trong đợt này (thêm 76).

---

## 2. Lỗi phát hiện và đã sửa

### L1 — Mô đun chết chứa lại đúng những lỗi đã sửa · **ĐÃ SỬA**

`alphaforge/history/submitted.py` không được bất kỳ đâu nhập, nhưng vẫn nằm
trong kho. Nội dung của nó là phiên bản cũ của bộ quét, mang đủ ba lỗi đã được
khắc phục ở `scanner.py`:

* `session.get(url)` gọi thẳng, bỏ qua BrainClient nên mất đăng nhập lại khi
  401, mất lùi thời gian khi 429 và mất thử lại khi máy chủ lỗi;
* so sánh ngày bằng cắt chuỗi mười ký tự, sai khi hai bản ghi lệch múi giờ;
* `if not submitted: continue` âm thầm vứt bản ghi thiếu ngày nộp.

Rủi ro thật: người sau nhập nhầm mô đun này và tưởng đang dùng bộ quét đã sửa.

**Sửa:** xóa tệp. Thêm kiểm thử chặn tệp quay lại, và một kiểm thử quét toàn bộ
gói để bảo đảm không mô đun nào ngoài `brain/client.py` gọi HTTP trực tiếp.

### L2 — Phụ thuộc vòng trong lớp mô hình ngôn ngữ · **ĐÃ SỬA**

```
llm.base -> llm.claude -> llm.base
llm.base -> llm.ollama -> llm.base
```

`base.py` nhập hai nhà cung cấp bên trong thân hàm `get_provider`, còn hai nhà
cung cấp lại kế thừa lớp trong `base` ở mức mô đun. Vòng này chạy được nhờ nhập
trễ, nhưng sẽ vỡ ngay khi ai đó dọn dẹp và đưa lệnh nhập lên đầu tệp.

**Sửa:** tách `get_provider` sang `alphaforge/llm/registry.py`. Chiều phụ thuộc
nay chỉ đi một hướng: `registry → claude/ollama → base`. Đường nhập cũ
`from alphaforge.llm import get_provider` giữ nguyên.

### L3 — Số placeholder không khớp số tham số · **ĐÃ SỬA**

`ResearchStore.save_lineage` có câu lệnh SQL với 11 dấu `?` nhưng chỉ truyền 10
giá trị, gây `sqlite3.ProgrammingError` ngay lần gọi đầu. Lỗi này do chính đợt
sửa L4 gây ra và được kiểm thử hồi quy bắt lại trước khi commit.

**Sửa:** bổ sung `lineage.generation_seed` vào tuple giá trị.

### L4 — Thiếu `generation_seed` nên không tái lập được lô sinh · **ĐÃ SỬA**

Bộ sinh nhận `--seed` để cho kết quả tất định, nhưng hạt giống đó không được
lưu ở đâu cả. Bản ghi alpha giữ được biểu thức, thiết lập mô phỏng và chiến
lược sinh, nhưng thiếu hạt giống thì không dựng lại được đúng lô đã sinh.

**Sửa:** thêm cột `generation_seed` vào `alphas` và `alpha_lineage`, thêm trường
vào `AlphaRecord` và `AlphaLineage`, truyền từ CLI xuống. Kho cũ tự di trú.

### L5 — Mức bão hòa chỉ là cờ đúng sai · **ĐÃ SỬA**

`ResearchProfile.is_saturated` trả về `True`/`False` nên không so sánh được hai
họ cùng vượt ngưỡng, và không có cách nào diễn đạt "đã thử nhiều nhưng vẫn đang
sinh lợi".

**Sửa:** thêm `saturation` (thang 0..1, kết hợp công sức bỏ ra với tỷ lệ đạt) và
`confidence` (dựa trên cỡ mẫu, để không kết luận mạnh từ nhóm ít quan sát). Cả
hai được đưa ra `describe_family` và `GenerationContext`.

### L6 — Trọng số chỉ áp ở mức họ cấu trúc · **ĐÃ SỬA**

Phát hiện khi viết kiểm thử cho mục 11. Bộ sinh tạo một cấu trúc mới trên đúng
trường dữ liệu đã khai thác kiệt thì không bị hạ ưu tiên chút nào, vì vân tay
mức `family` gồm cả cấu trúc lẫn trường, và cấu trúc mới tạo ra họ mới.

**Sửa:** `weight_for` tra theo ba mức, dừng ở mức hẹp nhất có dữ liệu:

| Mức | Ý nghĩa | Hệ số |
| --- | --- | --- |
| `family` | đúng cấu trúc, đúng trường | 1.0 |
| `template` | cùng cấu trúc, khác trường | 0.5 |
| `field` | khác cấu trúc, cùng trường | 0.3 |

Hệ số làm nhẹ phản ánh việc bằng chứng ở mức rộng hơn thì yếu hơn. Không mức
nào khớp thì trọng số bằng 1, tức không can thiệp.

### L7 — `jinja2` khai báo nhưng không dùng · **ĐÃ SỬA**

Bảng theo dõi phục vụ HTML bằng `Path.read_text()`, không dùng `Jinja2Templates`.
Gói này là phần thừa còn lại từ trước.

**Sửa:** bỏ khỏi `requirements.txt` và `pyproject.toml`. Thêm kiểm thử đối chiếu
mọi phụ thuộc khai báo với lệnh nhập thật trong mã.

---

## 3. Kết quả audit theo hạng mục

| Hạng mục kiểm tra | Kết quả | Ghi chú |
| --- | --- | --- |
| Import sai | **PASS** | 0 import trỏ tới mô đun không tồn tại |
| Circular import | **PASS** | đã sửa 1 vòng (L2), nay 0 |
| Mô đun không tồn tại | **PASS** | 0 |
| Mô đun chết | **PASS** | đã xóa 1 (L1), nay 0 |
| Triển khai trùng lặp | **PASS** | không còn |
| Database trùng lặp | **PASS** | một tệp SQLite, 10 bảng |
| Dependency thừa | **PASS** | đã bỏ `jinja2` (L7) và `pandas` |
| Path hardcode | **PASS** | 0 |
| Relative import sai | **PASS** | 0 |
| Cấu trúc package | **PASS** | nhất quán với README |

---

## 4. Kịch bản giả lập đã kiểm

### 4.1 Bộ quét lịch sử (mục 7) — **PASS**

| Kịch bản | Kết quả | Hành vi xác minh |
| --- | --- | --- |
| Một trang đầy 100 bản ghi | PASS | dừng đúng, `stopped_because=no_next_page` |
| Ba trang nối bằng `next` | PASS | thu đủ 15 bản ghi |
| `next = null` | PASS | dừng sạch |
| `next` có giá trị | PASS | đi tiếp trang sau |
| Không có trường `next` | PASS | coi như hết, không lỗi |
| Thiếu `dateSubmitted` | PASS | **giữ lại**, đếm vào `missing_date` |
| Thiếu `settings` | PASS | giữ lại, trường liên quan để `None` |
| Thiếu `regular` | PASS | giữ lại, biểu thức rỗng |
| Thiếu `is` | PASS | giữ lại, chỉ số để `None` |
| Thiếu cả ba khối | PASS | vẫn giữ được mã alpha |
| Sai kiểu dữ liệu | PASS | quy về `None`, không sập |
| Trùng `alpha_id` trong một trang | PASS | gộp, giữ bản mới |
| Trùng `alpha_id` giữa các trang | PASS | gộp, đếm vào `duplicates` |
| Bản ghi hỏng hoàn toàn | PASS | bỏ qua một bản, **không hủy cả lượt** |
| Thiếu `alpha_id` | PASS | bỏ qua, đếm riêng |
| HTTP 429 | PASS | nổi lên `RateLimitError` |
| HTTP 401 | PASS | nổi lên `AuthenticationError` |
| HTTP 500 | PASS | nổi lên `TransientError` |
| Timeout | PASS | xử lý như lỗi tạm thời |
| Thân phản hồi là HTML | PASS | nổi lên `BrainError` |
| Lỗi ở trang thứ hai | PASS | nổi lên, không trả dữ liệu thiếu mà im lặng |
| Máy chủ bỏ qua `offset` | PASS | dừng, `stopped_because=no_new_records` |

### 4.2 Lọc theo ngày và múi giờ (mục 8) — **PASS**

| Đầu vào | Kỳ vọng | Kết quả |
| --- | --- | --- |
| `2026-08-20T12:00:00Z` | `2026-08-20` | PASS |
| `2026-08-20T12:00:00+00:00` | `2026-08-20` | PASS |
| `2026-08-20T23:30:00-05:00` | `2026-08-21` | PASS |
| `2026-08-21T01:00:00+09:00` | `2026-08-20` | PASS |
| `2026-08-20` | `2026-08-20` | PASS |
| `2026-08-20T12:00:00.123456Z` | `2026-08-20` | PASS |
| Chuỗi không đọc được | `None`, không ném lỗi | PASS |

Mốc biên `start_date` và `end_date` đều **bao gồm**. Bản ghi lệch một giây ra
ngoài mỗi biên đều bị loại đúng. Một bản ghi ở `2026-07-31T20:00:00-05:00` được
quy đổi thành `2026-08-01` UTC và **lọt vào** khoảng, đúng như mong đợi.

### 4.3 Vân tay cấu trúc (mục 9) — **PASS**

Với `A = rank(ts_mean(returns,20))`, `B = …60`, `C = …volume,20`,
`D = rank(ts_rank(returns,20))`, `E = A`:

| Cặp | Kỳ vọng | Kết quả |
| --- | --- | --- |
| A vs E | `exact_duplicate` | PASS |
| A vs B | `same_structure_different_parameter` | PASS |
| A vs C | `same_structure_different_field` | PASS |
| A vs D | `same_fields_different_operators` | PASS |

Nhận diện token trên `group_neutralize(winsorize(rank(ts_mean(returns,20)),std=4),subindustry)`:

| Kiểm tra | Kết quả |
| --- | --- |
| `std` **không** bị coi là trường dữ liệu | PASS |
| `std` được nhận là tên đối số theo khóa | PASS |
| `subindustry` **không** bị coi là trường dữ liệu | PASS |
| `subindustry` được nhận là nhóm phân loại | PASS |
| `rank`, `ts_mean`, `ts_rank` được nhận là toán tử | PASS |
| `returns`, `volume` được nhận là trường dữ liệu | PASS |
| `20`, `60` được nhận là cửa sổ nhìn lại | PASS |
| `4` trong `std=4` **không** bị coi là cửa sổ | PASS |
| Không con số nào lọt vào tập trường dữ liệu | PASS |

### 4.4 Hàng đợi mô phỏng (mục 14) — **PASS**

| Kịch bản | Kết quả | Hành vi xác minh |
| --- | --- | --- |
| Mô phỏng thành công | PASS | chuyển `PASSED` |
| Biểu thức sai cú pháp | PASS | `FAILED` ngay, **đúng 1 lần gọi**, không thử lại |
| HTTP 401 | PASS | trả về hàng đợi, không đánh dấu hỏng |
| HTTP 429 | PASS | giảm tốc **cả hàng đợi**, trả bản ghi về |
| HTTP 500 rồi thành công | PASS | thử lại và hoàn tất |
| Timeout | PASS | coi như lỗi tạm thời |
| Lỗi liên tục | PASS | dừng sau `max_attempts`, **đúng 3 lần gọi** |
| `Retry-After` | PASS | tôn trọng thời gian máy chủ yêu cầu |
| Giới hạn đồng thời | PASS | đỉnh đồng thời không vượt trần đặt ra |
| Job trùng | PASS | 30 biểu thức, 30 lượt gọi, 0 trùng |
| Bảo vệ hạn mức | PASS | `--limit` chặn đúng số lượng |
| Khôi phục sau khi ngắt | PASS | `reset` đưa `RUNNING` về hàng đợi, chạy tiếp trọn vẹn |
| Dừng giữa chừng | PASS | bản ghi đang chạy quay lại hàng đợi |

### 4.5 Trí nhớ nghiên cứu trên 50 alpha (mục 10, 11) — **PASS**

Tập giả lập gồm bốn họ: bão hòa (25 alpha, hỏng gần hết), tốt (15, đạt đều),
mới (8, khả quan), hiếm (2, Sharpe cao nhất tập).

| Kiểm tra | Kết quả |
| --- | --- |
| Tần suất theo họ | PASS — 25/15/8/2 |
| Tần suất theo trường | PASS — biểu thức hai trường tính cho cả hai |
| Tần suất theo toán tử | PASS |
| `mean`, `median`, `p25`, `p75`, `min`, `max`, `stdev`, `count` | PASS — đủ cả tám |
| Thứ tự phân vị `p25 ≤ median ≤ p75` | PASS |
| Trung vị không bị ngoại lai kéo | PASS — max ≥ 3.4 nhưng trung vị < 2.0 |
| Điểm bão hòa xếp hạng đúng | PASS — họ tốt tuy thử nhiều vẫn không bị coi là bão hòa |
| Độ tin cậy thấp cho nhóm hai quan sát | PASS — < 0.2 |
| **Không kết luận mạnh từ cỡ mẫu nhỏ** | PASS — họ hiếm có Sharpe cao nhất nhưng **không** lọt bảng xếp hạng |
| Ưu tiên nghiên cứu | PASS — họ mới > họ bão hòa |
| Bộ sinh nhận đủ đầu vào | PASS — trường/toán tử/cửa sổ đã thử, bão hòa, ưu tiên |
| Bộ sinh ưu tiên hướng ít bão hòa | PASS |
| Bão hòa không làm bộ sinh trả rỗng | PASS |
| Bộ sinh chạy với ngữ cảnh dựng tay, không cần kho | PASS |

### 4.6 Phả hệ nghiên cứu (mục 12) — **PASS**

| Trường | Nơi lưu | Kết quả |
| --- | --- | --- |
| `research_id` | `alpha_lineage` | PASS |
| `hypothesis_id` | `alpha_lineage` | PASS |
| `experiment_id` | `alphas`, `alpha_lineage` | PASS |
| `variant_id` | `alphas`, `alpha_lineage` | PASS |
| `parent_alpha_id` | `alphas`, `alpha_lineage` | PASS |
| `generation_strategy` | `alphas`, `alpha_lineage` | PASS |
| `generation_seed` | `alphas`, `alpha_lineage` | PASS — **mới thêm ở L4** |

Chuỗi `Project → Hypothesis → Experiment → Variant → Alpha` được kiểm thử đầy
đủ. Lần ngược phả hệ có chặn vòng lặp khi dữ liệu hỏng tạo quan hệ cha con vòng
tròn. Alpha sinh từ alpha lịch sử ghi `source = "historical"`.

### 4.7 Di trú kho dữ liệu (mục 13) — **PASS**

| Kiểm tra | Kết quả |
| --- | --- |
| Không mất dữ liệu | PASS — 5 hàng cũ còn nguyên cả khóa lẫn giá trị |
| Không nhân bản | PASS — vẫn đúng 5 hàng |
| Cột mới được thêm | PASS — `family`, `template`, `windows_json`, `raw_json` |
| Chỉ mục được tạo | PASS — `idx_hist_family`, `idx_hist_submitted` |
| Khóa ngoại được bật | PASS — `PRAGMA foreign_keys = 1`, chèn mồ côi bị từ chối |
| **Chạy lần hai và lần ba không lỗi** | PASS |
| Kho mới tạo đủ bảng | PASS — 10/10 |

### 4.8 Bảng theo dõi (mục 17) — **PASS**

| Kiểm tra | Kết quả |
| --- | --- |
| Trang chủ hiển thị | PASS |
| 11 điểm cuối trên kho rỗng | PASS — không điểm cuối nào lỗi |
| **Không gọi BRAIN** | PASS — ứng dụng dựng được mà không cần client nào |
| Truy cập kho | PASS |
| Dữ liệu hỏng | PASS — JSON sai định dạng không làm sập điểm cuối |
| Thống kê bảng điều khiển | PASS — tiến độ và tỷ lệ đạt đúng |
| Chặn tham số quá lớn | PASS — `limit` ngoài khoảng trả 422 |
| Không trả cột `raw_json` cồng kềnh | PASS |

---

## 5. An toàn nộp alpha (mục 15) — **PASS**

Quét toàn kho cho kết quả:

* Chỉ tồn tại **hai** lệnh POST trong toàn bộ mã: `/authentication` và
  `/simulations`. Cái sau gửi **job mô phỏng**, không phải nộp alpha.
* Điểm cuối nộp alpha của BRAIN **không xuất hiện ở bất kỳ đâu**.
* `Status.SUBMITTED` **chỉ được đọc** một lần duy nhất trong `web/app.py` để
  đếm. **Không dòng mã nào gán trạng thái này.**
* `submit_simulation` là tên gọi dễ gây hiểu nhầm nhưng nó gửi mô phỏng;
  `pool.submit` là lời gọi của `ThreadPoolExecutor`.

Không tồn tại đường `generate → simulate → submit`. Alpha đạt ngưỡng chỉ chuyển
sang `PASSED` và dừng ở đó. Có kiểm thử khẳng định sau một lượt chạy đầy đủ,
số bản ghi `SUBMITTED` bằng 0.

---

## 6. Bảo mật (mục 16) — **PASS**

| Kiểm tra | Kết quả |
| --- | --- |
| Credential hardcode | PASS — 0 |
| Nhật ký in mật khẩu hoặc token | PASS — 0 |
| `Credentials` có `repr=False` | PASS — `repr()` và `str()` đều chỉ hiện email |
| `.env` trong `.gitignore` | PASS — kèm `.env.*` |
| `credentials.json` trong `.gitignore` | PASS — kèm `credentials.txt`, `brain_credentials.json` |
| Khóa API, chứng chỉ, token | PASS — `*.key`, `*.pem`, `*_token` |
| Tệp bí mật bị theo dõi | PASS — 0 |
| Khóa Claude chỉ từ biến môi trường | PASS — có kiểm thử khẳng định khóa trong tệp cấu hình bị bỏ qua |
| `--verbose` không bật DEBUG thư viện HTTP | PASS |

---

## 7. Phụ thuộc (mục 18)

| Gói | Khai báo | Được nhập | Kết luận |
| --- | --- | --- | --- |
| `requests` | có | có | giữ |
| `PyYAML` | có | có | giữ |
| `python-dotenv` | có | có | giữ |
| `fastapi` | có | có | giữ |
| `uvicorn` | có | có | giữ |
| `jinja2` | có | **không** | **đã bỏ** |
| `anthropic` | tùy chọn `[llm]` | nhập trễ | đúng vị trí |

Không thêm phụ thuộc mới nào trong đợt audit này.

---

## 8. Tệp thay đổi

**Xóa**
* `alphaforge/history/submitted.py` — mô đun chết mang lỗi cũ

**Thêm**
* `alphaforge/llm/registry.py` — cắt vòng phụ thuộc
* `tests/test_audit_regressions.py` — 18 kiểm thử hồi quy
* `tests/test_mock_scenarios.py` — 35 kịch bản giả lập
* `tests/test_research_dataset.py` — 23 kiểm thử trên tập 50 alpha
* `AUDIT_REPORT.md`

**Sửa**
* `alphaforge/storage/db.py` — cột `generation_seed` ở hai bảng
* `alphaforge/research/models.py` — trường `generation_seed`
* `alphaforge/research/store.py` — lưu hạt giống, sửa lệch placeholder
* `alphaforge/research/memory.py` — `saturation`, `confidence`, trọng số ba mức
* `alphaforge/llm/base.py` — bỏ `get_provider`
* `alphaforge/llm/__init__.py` — lấy `get_provider` từ registry
* `alphaforge/cli.py` — truyền hạt giống xuống kho
* `requirements.txt`, `pyproject.toml` — bỏ `jinja2`

---

## 9. Kiểm thử đã thêm

| Tệp | Số lượng | Nội dung |
| --- | --- | --- |
| `test_audit_regressions.py` | 18 | mỗi lỗi ở mục 2 có ít nhất một kiểm thử chặn tái phát |
| `test_mock_scenarios.py` | 35 | phản hồi máy chủ, mã lỗi HTTP, múi giờ, hàng đợi |
| `test_research_dataset.py` | 23 | thống kê, bão hòa, ưu tiên, bộ sinh trên 50 alpha |
| **Tổng** | **76** | 214 → **290** |

Một số kiểm thử mang tính bất biến kiến trúc chứ không chỉ kiểm tra chức năng:

* không mô đun nào ngoài `brain/client.py` được gọi HTTP trực tiếp;
* gói `llm` phải là đồ thị một chiều;
* mọi phụ thuộc khai báo phải thực sự được nhập;
* một lượt chạy đầy đủ không được sinh ra bản ghi `SUBMITTED` nào.

Những kiểm thử này bắt lỗi khi ai đó vô tình phá vỡ nguyên tắc, chứ không đợi
tới lúc lỗi biểu hiện ra ngoài.

---

## 10. Vấn đề còn tồn tại

| Vấn đề | Mức | Ghi chú |
| --- | --- | --- |
| `family` và `fingerprint` lưu cùng giá trị | thấp | dư một cột, giữ để tương thích ngược |
| Bảng theo dõi quét lại toàn bộ lịch sử mỗi 60 giây | trung bình | sẽ chậm ở quy mô hàng trăm nghìn alpha, cần cache |
| Ngưỡng chọn theo phán đoán | trung bình | `MIN_SAMPLE=8`, `SATURATION_REFERENCE=50`, `RECOVERY_STREAK=10`, hai hệ số làm nhẹ 0.5 và 0.3 — chưa hiệu chỉnh bằng dữ liệu thật |
| `submit_simulation` là tên dễ gây hiểu nhầm | thấp | gửi job mô phỏng, không nộp alpha; đổi tên sẽ phá tương thích |
| Chưa có lint hay kiểm tra kiểu | thấp | kho không cấu hình `ruff` hay `mypy`; không tự thêm vì chưa được yêu cầu |

---

## 11. Chưa kiểm được vì không truy cập BRAIN — **NOT TESTED**

Những mục dưới đây **không được đánh dấu PASS**. Chúng chỉ được kiểm bằng giả
lập dựng theo tài liệu API, nên chỉ chứng minh mã xử lý đúng *hình dạng dữ liệu
giả định*, không chứng minh hình dạng đó khớp thực tế.

| Hạng mục | Trạng thái | Cần gì để kiểm |
| --- | --- | --- |
| Hình dạng thật của `/users/self/alphas` | NOT TESTED | một lượt quét thật với khoảng ngày hẹp |
| Máy chủ có chấp nhận `status!=UNSUBMITTED` | NOT TESTED | nếu không, bộ lọc bị bỏ qua và trả về toàn bộ alpha |
| Trần `limit` thật của điểm cuối | NOT TESTED | mã đang giả định 100 |
| Trường `next` là URL hay `null` | NOT TESTED | đã xử lý cả hai dạng |
| Xác thực thật, gồm sinh trắc học | NOT TESTED | mã có nhánh xử lý nhưng chưa chạy thật |
| Hành vi 429 và `Retry-After` thật | NOT TESTED | giá trị và tần suất thật chưa rõ |
| Vòng đời mô phỏng thật | NOT TESTED | thời gian thăm dò, mã trạng thái trung gian |
| Hình dạng phản hồi tương quan | NOT TESTED | mã dò theo tên cột nên chịu được thay đổi |
| Tên chỉ số trong khối `is` | NOT TESTED | `sharpe`, `fitness`, `turnover`, `margin` theo tài liệu |

**Khuyến nghị khi có thông tin đăng nhập:** chạy `history scan` với khoảng một
tuần và `--max-records 20` trước, đọc phần `diagnostics` trong kết quả, đối
chiếu trước khi tin vào lượt quét lớn.

---

## 12. Tóm tắt

| | Số lượng |
| --- | --- |
| Lỗi phát hiện | 7 |
| Lỗi đã sửa | 7 |
| Lỗi còn mở | 0 |
| Kiểm thử thêm | 76 |
| Tổng kiểm thử | 290, toàn bộ pass |
| Hạng mục PASS | 8 nhóm |
| Hạng mục NOT TESTED | 9, tất cả đều cần BRAIN |
| Hạng mục FAIL | 0 |

Mã nguồn đã được xác minh ở mức có thể xác minh mà không cần máy chủ. Hệ thống
sẵn sàng chạy khi được cấp thông tin đăng nhập, nhưng lượt chạy thật đầu tiên
vẫn nên coi là bước kiểm chứng, không phải bước vận hành.

---

# Phụ lục: mở rộng thành Alpha Research Hub

**Ngày:** 2026-08-23 (đợt hai)
**Phạm vi:** chỉ mã nguồn và môi trường cục bộ. Không credentials, không gọi BRAIN.

## Cổng kiểm tra

| Cổng | Kết quả |
| --- | --- |
| `pytest -q` | **PASS** — 399 passed (trước đợt này: 290) |
| `python -m compileall .` | **PASS** — exit 0 |
| Nhập toàn bộ package | **PASS** — 39/39 module |
| `alphaforge --help` | **PASS** — 16 nhóm lệnh |
| Phụ thuộc vòng | **PASS** — 0 |
| TODO chưa xử lý | **PASS** — 0 |
| Lệnh CLI cũ vẫn chạy | **PASS** — 11/11, socket bị chặn |

## Đã triển khai

| Phase | Nội dung | Mô đun |
| --- | --- | --- |
| 1 | Trí nhớ phân biệt 5 nguồn, độ phủ 9 chiều | `research/memory.py` (mở rộng) |
| 2 | Thiếu hụt trên 8 chiều, gồm thiếu độ phủ | `research/gap.py` (mới) |
| 3 | Điểm ưu tiên 4 thành phần kèm lý do | `research/priority.py` (mới) |
| 4 | Thí nghiệm một thay đổi mỗi lần | `research/experiment.py` (mới) |
| 5 | Kế hoạch sinh đã kiểm tra | `research/plan.py` (mới) |
| 6 | Bộ sinh nhận kế hoạch, giữ 3 chiến lược cũ | `generator/engine.py` (không đổi) |
| 7 | Phả hệ thêm `source_alpha_id` | `research/models.py`, `store.py` |
| 8 | Kiểm tra biểu thức trước mô phỏng | `pipeline/validation.py` (mới) |
| 9 | Đường duy nhất vào hàng đợi | `pipeline/generation.py` (mới) |
| 10 | Chuẩn hóa kết quả: `evaluation_status`, `rank`, `robustness` | `storage/db.py` |
| 11 | Độ bền 6 phép kiểm, 3 hồ sơ | `pipeline/robustness.py` (mới) |
| 12 | Bậc thang tương quan, bảng `correlation_results` | `pipeline/evaluation.py` (mới) |
| 13 | `CANDIDATE`, `HUMAN_REVIEW` | `storage/db.py` |
| 14 | `experiment_outcome` phản hồi về trí nhớ | `research/memory.py` |
| 15 | Báo cáo thí nghiệm và kết luận | `research/report.py` (mới) |
| 16 | 7 điểm cuối web mới | `web/app.py` |
| 17 | 5 nhóm lệnh mới, giữ nguyên lệnh cũ | `cli.py` |

## Lỗi phát hiện trong quá trình xây dựng

### L8 — Biến thể thí nghiệm bị bọc thêm toán tử · **ĐÃ SỬA**

`ExperimentEngine.build_plan` dùng chiến lược `mutate`, nghĩa là biến thể đi qua
bộ sinh và bị bọc thêm `rank(...)`, `zscore(...)` — **phá vỡ đúng cái mà thí
nghiệm có kiểm soát muốn cô lập**. Thí nghiệm khảo sát cửa sổ nhìn lại lại đo
ra ảnh hưởng của toán tử bọc ngoài.

Phát hiện khi chạy thử toàn vòng và thấy báo cáo hiện mọi biến thể với cỡ mẫu
bằng không.

**Sửa:** thêm chế độ `direct` cho kế hoạch, nghĩa là biểu thức đã xác định và
không cần sinh gì thêm. Biến thể vào hàng đợi nguyên vẹn.

### L9 — Alpha không nối được về biến thể · **ĐÃ SỬA**

Không có `variant_id`, báo cáo không quy được kết quả về biến thể nào và mọi
biến thể hiện ra với cỡ mẫu bằng không, dù thí nghiệm đã chạy xong.

**Sửa:** `generate_and_queue` nhận ánh xạ biểu thức sang mã biến thể và gắn lên
bản ghi sau khi thêm.

### L10 — Lệch placeholder lần thứ hai · **ĐÃ SỬA**

Thêm cột `generation_seed` vào `alpha_lineage` làm câu lệnh có 12 dấu hỏi nhưng
tuple chỉ có 11 giá trị.

**Sửa:** bổ sung giá trị thiếu, **và thêm kiểm thử bất biến quét toàn kho, đếm
dấu hỏi so với số tham số trong mọi lệnh INSERT có thể đếm tĩnh.** Đã xác minh
kiểm thử bắt được lỗi bằng cách cố tình phá lại code.

### L11 — `min_operators=1` loại nhầm biểu thức hợp lệ · **ĐÃ SỬA**

`close * -open` không có lời gọi toán tử nào nhưng vẫn là biểu thức hợp lệ.

**Sửa:** mặc định về 0, vẫn cấu hình được cho thí nghiệm nào cần.

### L12 — Hai công thức ưu tiên cho cùng một khái niệm · **ĐÃ SỬA**

`analyzer.research_gaps` dùng `median / sqrt(count)`, còn `ResearchPriority` dùng
tích bốn thành phần. Hai nơi trả về hai con số khác nhau cho cùng một họ.

**Sửa:** hợp nhất, `analyzer.research_gaps` nay gọi `ResearchPriority`. Giữ
nguyên chữ ký và các khóa cũ để mã hiện có không phải sửa.

## Kiểm thử thêm

| Tệp | Số lượng | Nội dung |
| --- | --- | --- |
| `test_research_memory_sources.py` | 35 | nguồn dữ liệu, độ phủ, thiếu hụt, ưu tiên |
| `test_experiment_pipeline.py` | 63 | kiểm tra, kế hoạch, thí nghiệm, độ bền, thẩm định, ứng viên |
| bổ sung vào `test_web.py` | 11 | điểm cuối nghiên cứu mới |
| **Tổng thêm** | **109** | 290 → **399** |

Kiểm thử đáng chú ý:

* `test_experiment_variants_are_queued_unchanged` — chặn L8 tái phát;
* `test_every_insert_binds_the_right_number_of_values` — chặn cả lớp lỗi L10;
* `test_submission_requires_human_controlled_state` — hệ thống không tự nộp;
* `test_evaluation_never_produces_submitted_on_its_own`;
* `test_missing_data_is_reported_as_unavailable_not_as_failure` — thiếu dữ liệu
  khác với không đạt;
* `test_small_sample_does_not_produce_a_strong_claim`.

## Tương thích ngược — **PASS**

* Ba chiến lược sinh giữ nguyên, `GeneratorEngine` không đổi hành vi.
* Toàn bộ 11 lệnh CLI cũ chạy đúng như trước.
* `Status` chỉ **thêm** giá trị, không đổi hay bỏ giá trị nào.
* `analyzer.research_gaps` giữ chữ ký và mọi khóa cũ.
* Kho SQLite cũ tự di trú, không phải tạo lại.
* Không xóa mô đun nào đang được dùng.

## An toàn — **PASS**

Quét lại sau khi thêm mã: vẫn chỉ có hai lệnh POST trong toàn kho
(`/authentication` và `/simulations` — gửi job mô phỏng). Điểm cuối nộp alpha
của nền tảng không xuất hiện ở đâu.

`Status.SUBMITTED` nay có một nơi ghi: `EvaluationPipeline.mark_submitted`. Hàm
này **từ chối** nếu alpha chưa ở `CANDIDATE` hoặc `HUMAN_REVIEW`, và chỉ ghi
chép việc người dùng nói rằng họ đã tự nộp. Không có lệnh gọi mạng nào trong đó.

## Còn tồn tại

| Vấn đề | Mức | Ghi chú |
| --- | --- | --- |
| Phép kiểm độ nhạy cần chỉ số biến thể | trung bình | báo `unavailable` khi thiếu, không đoán |
| Phép kiểm theo năm cần phân rã `pnlByYear` | trung bình | chưa rõ máy chủ có trả về không |
| Ngưỡng chọn theo phán đoán | trung bình | `min_sample`, `SATURATION_REFERENCE`, hệ số làm nhẹ 0.5/0.3 |
| Giao diện web mới chỉ có API | thấp | trang HTML chưa hiển thị phần mới |
| `family` và `fingerprint` trùng giá trị | thấp | giữ để tương thích ngược |

## Chưa kiểm được — **NOT TESTED**

Toàn bộ mục NOT TESTED của đợt trước vẫn giữ nguyên. Bổ sung:

| Hạng mục | Trạng thái | Lý do |
| --- | --- | --- |
| Máy chủ có trả `pnlByYear` không | NOT TESTED | quyết định phép kiểm theo năm có chạy được |
| Tên khóa thật của phân rã theo năm | NOT TESTED | mã dò bốn biến thể tên khóa |
| Ngưỡng độ bền hợp lý ở từng khu vực | NOT TESTED | cần dữ liệu thật để hiệu chỉnh |
| Tương quan thật sau bước lọc cấu trúc | NOT TESTED | chưa biết bước lọc cục bộ tiết kiệm được bao nhiêu lượt gọi |
