# Plan: Pass 2 (OCR) chạy trên Kaggle Notebook

> Trạng thái: **Lên kế hoạch — CHƯA viết code, đang chờ source thật của `code_extractor.py`/`header_ocr.py` để port đúng logic.**
> Liên quan tới: `Claude.md` (dự án gốc, chạy local WSL2) — file này KHÔNG thay thế `Claude.md`, chỉ mô tả một môi trường thực thi thay thế cho Pass 2.
> Cập nhật lần cuối: 2026-07-09 | Người thực hiện: Thế Anh

---

## 1. Mục tiêu

Chạy phần Pass 2 (OCR trích `contract_code`) cho các dòng còn lại của 4024 hợp đồng trên **Kaggle Notebook** thay vì local WSL2, để tận dụng **GPU T4 miễn phí (compute capability 7.5)** — điều máy local không làm được vì GPU sẵn có (Quadro M1000M, CC 5.0) không đủ điều kiện tối thiểu CC≥7.0 của `paddlepaddle-gpu` 3.x (đã xác nhận và loại trừ ở `Claude.md` mục 6).

**Không làm lại:**
- Pass 1 (đã xong, 4024 dòng đã có trong Postgres)
- Logic walk cây Drive / xử lý shortcut chết (đã fix ở `Claude.md` mục 5)
- Regex trích mã hợp đồng (đã trưởng thành qua nhiều đợt sửa bug thật — port nguyên, không viết lại)

## 2. Vì sao chuyển sang Kaggle (và vì sao KHÔNG phải vì tốc độ CPU)

Local đã đạt ~15s/file với 4 worker song song (~16,8 giờ cho 4024 file) — đây **không phải** điểm nghẽn thảm hại. Lý do thật sự đáng chuyển là:
- Đòn bẩy hiệu năng lớn nhất còn lại ở local là mkldnn — **chưa test được** (crash trước đó, kế hoạch test cô lập chưa thực hiện)
- Kaggle cung cấp GPU T4 mà local không có lựa chọn tương đương — đường thay thế song song, không phụ thuộc vào việc mkldnn có test thành công hay không

## 3. Rủi ro / điều kiện tiên quyết (BẮT BUỘC xử lý trước Phase 1)

1. **Bảo mật dữ liệu** — dữ liệu là hợp đồng Legal thật của Urbox (SMBC, Prudential, AIA, Vietnam Airlines, HSBC...). PDF sẽ tạm thời đi qua RAM của hạ tầng Kaggle (thuộc Google). **Cần xác nhận với hanh.ph/Legal trước khi chạy full** — đây là gate chặn, không phải formality.
2. **Refresh token hết hạn sau 7 ngày** — app OAuth đang ở chế độ Testing (`Claude.md` mục 7). Nếu quá 7 ngày kể từ lần đăng nhập cuối trên WSL2, phải đăng nhập lại trước khi export sang Kaggle Secrets. Nếu dự định chạy nhiều đợt rải rác trong nhiều tuần, cân nhắc đưa app OAuth sang Production để bỏ giới hạn này.
3. **Không kết nối thẳng Kaggle → Postgres local** — `localhost:5433` trên WSL2 không có địa chỉ public, và mở port ra ngoài là rủi ro bảo mật không cần thiết. Dùng CSV làm cầu nối (xem Phase 7), không dựng Postgres cloud riêng (đã đánh giá là over-engineering cho quy mô này).

## 4. Việc còn thiếu trước khi viết code (đang chờ)

- [ ] Source thật `ocr/code_extractor.py` — các regex (`SO_LINE_RE`, `SO_LINE_NOVOWEL_RE`, `BLANK_NUM_RE`, `STANDALONE_DDMM_RE`, `CODE_RE_LG`) và logic loại trừ "đính kèm"/"nguyên tắc". Không đoán lại từ mô tả — rủi ro thụt lùi so với các lần sửa bug thật đã tích luỹ.
- [ ] Source thật `ocr/header_ocr.py` — chữ ký hàm `render_header_crop()` (input/output type), logic `_has_so_line()` (escalation 0.30→0.55), cách khởi tạo `PaddleOCR(...)` và parse `result["rec_texts"]`.
- [ ] Logic "cross-check" quyết định `confidence = high/low` — nghi là so khớp với `ngay_ky` đã parse ở Pass 1, nhưng chưa xác nhận field/cơ chế thật. Ảnh hưởng trực tiếp tới cột cần export trong `manifest.csv` (Phase 2).
- [ ] (Tuỳ chọn) Helper hữu ích trong `ocr/pdf_fetcher.py` ngoài phần multi-thread service (không cần port multi-thread vì Kaggle bản đầu chạy tuần tự, không song song fetch).

## 5. Kiến trúc pipeline

```
Authenticate (OAuth refresh token qua Kaggle Secrets)
    ↓
Đọc manifest.csv (export từ Postgres, KHÔNG tự walk lại Drive)
    ↓
Với mỗi drive_file_id chưa có trong checkpoint:
    ↓
Tải PDF vào RAM (io.BytesIO — KHÔNG ghi ra đĩa)
    ↓
[MỚI, chưa có ở local] Diagnostic: PyMuPDF get_text() trên crop 30% đầu trang
    ├─ Match "Số:" trong text layer thật → contract_code_source = 'pdf_text', BỎ QUA OCR
    └─ Không có → render crop thành ảnh (dpi=200) → PaddleOCR (GPU)
                    → thiếu dòng "Số:" → escalation crop 0.55 (giữ nguyên logic local)
                    → regex extract → contract_code_source = 'ocr_tier1'
    ↓
Ghi 1 dòng vào CSV kết quả NGAY (flush mỗi dòng, không gom batch)
    ↓
Giải phóng buf/doc khỏi RAM (không cần "xoá file" vì chưa từng ghi đĩa)
```

**Về bước Diagnostic (mới):** không có trong pipeline local hiện tại. Nếu PDF có text layer thật, bỏ qua OCR hoàn toàn — nhanh hơn đáng kể. Đáng cân nhắc thêm cả vào code local sau này (độc lập với việc có chạy Kaggle hay không).

## 6. Các Phase thực hiện

### Phase 0 — Pre-flight
- [ ] Xác nhận bảo mật với hanh.ph/Legal (mục 3.1)
- [ ] Kiểm tra hạn refresh token (mục 3.2)
- [ ] Chọn tập mẫu validation 20-30 file — ưu tiên nhóm đã có `contract_code` xác nhận từ local, để so sánh đối chiếu (không đoán độ tin cậy)

### Phase 1 — Setup môi trường Kaggle
- [ ] Bật **Internet: On** và **Accelerator: GPU T4** trong Notebook Options
- [ ] Cài `paddlepaddle-gpu` (xác nhận đúng bản CUDA của Kaggle image bằng `!nvidia-smi`/`!nvcc --version`, không giả định), `paddleocr==3.7.0`, `PyMuPDF`, `psycopg2-binary`
- [ ] Tạo 3 Kaggle Secrets: `GDRIVE_REFRESH_TOKEN`, `GDRIVE_CLIENT_ID`, `GDRIVE_CLIENT_SECRET` (lấy từ `token.json`/`credential.json` local)

### Phase 2 — Chuẩn bị input
- [ ] Export manifest từ Postgres (chỉ phần `contract_code IS NULL`):
  ```sql
  COPY (
      SELECT drive_file_id, file_path, nhom, brand, ten_hop_dong
      FROM contracts
      WHERE contract_code IS NULL
  ) TO '/tmp/manifest.csv' WITH CSV HEADER;
  ```
  → **Cần bổ sung cột phục vụ cross-check** sau khi xác nhận mục 4 (nghi là `ngay_ky`)
- [ ] Upload `manifest.csv` làm Kaggle Dataset riêng (private, nhẹ vì chỉ text)
- [ ] Port nguyên regex + logic crop/escalation từ `code_extractor.py`/`header_ocr.py` vào notebook — copy, không viết lại
- [ ] Giữ `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=False` mặc định, nhưng **test riêng trên Kaggle** — môi trường CUDA khác local, không giả định bug mkldnn giống hệt

### Phase 3 — Checkpoint & resilience
- [ ] CSV kết quả ghi append-mode, flush sau mỗi dòng
- [ ] Đầu notebook đọc lại checkpoint cũ, bỏ qua `drive_file_id` đã xử lý — cho phép resume nếu session Kaggle bị ngắt (giới hạn 12 giờ/phiên)
- [ ] Cân nhắc chia chạy theo `nhom` (giống local) để dễ theo dõi tiến độ và khoanh vùng lỗi (nhóm BẢO HIỂM đã có tỷ lệ soi tay bất thường ở local)

### Phase 4 — Validation trước khi chạy full
- [ ] Chạy tập mẫu 20-30 file đã chọn ở Phase 0
- [ ] So sánh từng dòng `contract_code` Kaggle vs kết quả local đã biết — không chỉ nhìn tỷ lệ tổng
- [ ] Nếu lệch, nghi ngờ theo thứ tự: (a) PaddleOCR không hoàn toàn deterministic giữa GPU/CPU, (b) khác biệt phiên bản CUDA/paddle gây lỗi khác mkldnn cũ ở local

### Phase 5 — Chạy full phần còn lại
- [ ] Chạy theo nhóm hoặc toàn bộ tuỳ kết quả Phase 4
- [ ] Theo dõi rate limit Drive API — chỉ thêm retry/backoff nếu thực sự gặp lỗi `429` (tránh over-engineer sớm)

### Phase 6 — Merge kết quả về Postgres local
- [ ] Tải CSV kết quả về từ tab Output của Kaggle
- [ ] UPSERT qua `drive_file_id UNIQUE` (đúng cơ chế idempotent đã thiết kế sẵn ở `Claude.md` mục 4):
  ```sql
  CREATE TEMP TABLE pass2_staging (
      drive_file_id TEXT,
      contract_code TEXT,
      contract_code_source TEXT,
      contract_code_confidence TEXT
  );
  \COPY pass2_staging FROM 'pass2_results.csv' WITH CSV HEADER;

  UPDATE contracts c
  SET
      contract_code            = s.contract_code,
      contract_code_source     = s.contract_code_source,
      contract_code_confidence = s.contract_code_confidence
  FROM pass2_staging s
  WHERE c.drive_file_id = s.drive_file_id;
  ```

### Phase 7 — Sau khi chạy xong
- [ ] Đo tỷ lệ thật: `pdf_text` (bỏ qua OCR) vs `ocr_tier1` vs `NULL` — số liệu cần cho câu hỏi mở "Tier 2 PaddleOCR-VL có đáng cài không" (`Claude.md` mục 9)
- [ ] Soi tay các dòng `confidence=low` hoặc `contract_code IS NULL`
- [ ] Bắt đầu Pass 3 (fuzzy join qua tên) — chạy local, không liên quan Kaggle

## 7. Cố tình để ngoài phạm vi plan này

- Test cô lập mkldnn ở local — độc lập, có thể làm song song trong lúc chờ Kaggle chạy
- Dựng Postgres cloud riêng — đã đánh giá over-engineering cho quy mô 4024 dòng, giữ CSV làm cầu nối
- Độ tin cậy escalation crop trên quy mô lớn — quan sát qua Phase 5/7, chưa cần quyết định trước
- Song song hoá fetch/OCR trên Kaggle (ThreadPoolExecutor/ProcessPoolExecutor như local) — bản đầu chạy tuần tự cho đơn giản; chỉ thêm nếu GPU đã đủ nhanh mà fetch tuần tự trở thành nút thắt mới

## 8. Câu hỏi mở

- [ ] Cross-check confidence dùng field/cơ chế gì thật sự? (mục 4)
- [ ] Kaggle GPU T4 có cho kết quả OCR khác đáng kể so với CPU local không, trên cùng 1 tập file mẫu? — cần dữ liệu thật từ Phase 4, không suy đoán trước
- [ ] Sau khi có tỷ lệ `pdf_text` thật, native-text-first có đáng port ngược vào pipeline local không?
