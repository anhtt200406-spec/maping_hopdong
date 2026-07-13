# Danh mục hợp đồng — Google Drive → Postgres

Trích xuất danh mục hợp đồng (nhóm/brand/tên file) từ cây thư mục Google Drive (Legal) đổ vào Postgres, sau đó dùng OCR để bóc **mã hợp đồng** từ nội dung PDF. Đây là bước đệm cho bài toán xa hơn: *mapping mã hợp đồng ↔ record ở một DB khác* (định danh theo tên, không theo mã).

## Trạng thái hiện tại

- **Pass 1 — xong.** 4024 hợp đồng đã đổ vào Postgres.
- **Pass 2 — code xong**, đã song song hoá + tối ưu tốc độ OCR, đang chạy thử theo từng nhóm. **Chưa chạy full 4024 file.**
- **Pass 3 — chưa làm** (nối `contract_code` sang DB khác).

## Kiến trúc pipeline

```
Pass 1 (crawl/)          Pass 2 (ocr/)                    Pass 3 (chưa làm)
Duyệt cây Drive     →    Tải PDF + OCR bóc mã       →     Nối contract_code
(chỉ tên+ID,              hợp đồng, ghi contract_code      sang record ở DB khác
không tải file)           vào cùng bảng                    (exact join → fuzzy)
```

- **Pass 1** duyệt cây Google Drive bằng Drive API (chỉ lấy tên + ID, không tải nội dung file) — 3 cấp **Nhóm → Brand → file PDF** — ghi `nhom`, `brand`, `ten_hop_dong` vào Postgres.
- **Pass 2** tải nội dung từng PDF, thử đọc text layer trước (PDF native), nếu là bản scan thì OCR (PaddleOCR PP-OCRv6) phần đầu trang, dùng regex bóc mã hợp đồng và cross-check bằng ngày tháng lặp lại trong văn bản để tự chấm `confidence` (`high`/`low`).
- **Pass 3** (chưa triển khai) sẽ join `contract_code` sang DB khác, kết quả chia 3 nhóm: khớp chắc chắn / khớp mờ cần soi tay / không khớp.

## Cấu trúc thư mục

```
code/
├── config.py, drive_auth.py, postgres_store.py   # dùng chung: đọc .env, OAuth Drive, DB
├── requirements.txt
├── .env.example                                   # copy thành .env rồi điền giá trị thật
├── credentials.json                                # OAuth client (tự lấy từ Google Cloud Console, xem Bước 4)
├── token.json                                       # tự sinh ra lúc chạy lần đầu, KHÔNG tạo tay
├── crawl/          # Pass 1
│   ├── drive_walker.py                  # duyệt đệ quy cây Drive, resolve shortcut
│   ├── normalize.py                     # chuẩn hoá tên nhóm/brand, dựng rows để upsert
│   ├── drive_contracts_to_postgres.py   # entry point Pass 1 — cào dữ liệu
│   └── check_broken_shortcuts.py        # chỉ kiểm tra nhánh Drive lỗi quyền, không ghi DB
└── ocr/            # Pass 2
    ├── pdf_fetcher.py                   # tải PDF từ Drive
    ├── header_ocr.py                    # crop + chạy PaddleOCR
    ├── code_extractor.py                # regex bóc mã + cross-check
    ├── extract_contract_codes.py        # entry point Pass 2 (song song hoá)
    ├── test_pdf.py                      # debug OCR 1 file, không đụng Drive/Postgres
    └── bench_ocr.py                     # benchmark cấu hình OCR (công cụ phụ trợ)
```
Không dùng package/`__init__.py` — mỗi entry point tự thêm `code/` vào `sys.path` lúc chạy. `credentials.json` và `token.json` **không** có sẵn trong repo (nằm trong `.gitignore` vì là thông tin đăng nhập riêng) — bạn phải tự tạo theo hướng dẫn bên dưới.

## Yêu cầu hệ thống

- **Python 3.12** — đây là bản duy nhất đã test và xác nhận chạy được. **Không dùng Python 3.14** (`paddlepaddle` từng không có wheel hỗ trợ, đã xác nhận thực tế). Các bản 3.9–3.11/3.13 chưa được test trong dự án này.
- **Postgres** đã cài và chạy sẵn (local hoặc từ xa).
- **Tài khoản Google** có quyền truy cập (ít nhất "Viewer") vào thư mục Drive chứa hợp đồng, và quyền tạo Google Cloud project để lấy OAuth credentials.
- **Linux/WSL:** cần thêm gói hệ thống `libgomp1` cho `paddlepaddle` (`sudo apt-get install libgomp1`). **Windows:** không cần bước này — wheel `paddlepaddle` cho Windows tự đóng gói runtime.

## Hướng dẫn chạy từ đầu (người mới clone về)

Toàn bộ lệnh `python ...` chạy từ thư mục `code/`.

### Bước 1 — Clone & vào thư mục code

```bash
git clone https://github.com/anhtt200406-spec/maping_hopdong.git
cd maping_hopdong/code
```

### Bước 2 — Tạo venv & cài thư viện

```bash
python3.12 -m venv .venv          # Windows: py -3.12 -m venv .venv
source .venv/bin/activate         # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Bước 3 — Tạo database Postgres

Script sẽ **tự tạo bảng** `contracts` (và tự thêm cột khi cần) trong lần chạy đầu tiên — bạn chỉ cần tự tạo sẵn **database rỗng**:

```bash
# ví dụ dùng psql, đổi tên DB/user tuỳ ý (phải khớp với .env ở Bước 5)
createdb contracts_db
```

### Bước 4 — Lấy OAuth credentials (`credentials.json`)

Đây là bước hay bỡ ngỡ nhất với người mới — làm đúng thứ tự sau, chỉ cần làm **1 lần**:

1. Vào [Google Cloud Console](https://console.cloud.google.com/) → tạo project mới (hoặc chọn project có sẵn).
2. Vào **APIs & Services → Library**, tìm **Google Drive API** → bấm **Enable**.
3. Vào **APIs & Services → OAuth consent screen** → chọn loại **External** → điền tên app + email liên hệ → ở phần **Test users**, thêm đúng địa chỉ Gmail bạn sẽ dùng để đăng nhập Drive (tài khoản đã được cấp quyền xem thư mục hợp đồng) → Save.
4. Vào **APIs & Services → Credentials → Create Credentials → OAuth client ID** → Application type chọn **Desktop app** → đặt tên bất kỳ → Create.
5. Bấm **Download JSON**, đổi tên file vừa tải thành `credentials.json`, copy vào `code/credentials.json`.

> Vì app đang ở chế độ **Testing** (chưa submit Google duyệt), lần đăng nhập đầu tiên trình duyệt sẽ cảnh báo *"Google chưa xác minh ứng dụng này"* — đây là bình thường, bấm **Advanced/Nâng cao** → **Go to (tên app) (unsafe)** để tiếp tục, miễn là bạn tự tạo project này. Refresh token cũng sẽ tự hết hạn sau 7 ngày ở chế độ Testing — hết hạn thì đăng nhập lại là xong, không phải lỗi.

### Bước 5 — Cấu hình `.env`

```bash
cp .env.example .env
```

Mở `code/.env` và điền:

| Biến | Cách lấy |
|---|---|
| `ROOT_FOLDER_ID` | Mở thư mục gốc chứa hợp đồng trên Drive bằng trình duyệt, copy đoạn ID trong URL: `drive.google.com/drive/folders/`**`<ID_Ở_ĐÂY>`** |
| `PG_DBNAME`, `PG_USER`, `PG_PASSWORD`, `PG_HOST`, `PG_PORT` | Khớp với database đã tạo ở Bước 3 (port mặc định Postgres là `5432`, đổi lại nếu máy bạn cấu hình cổng khác) |

### Bước 6 — Chạy Pass 1: cào dữ liệu từ Drive

```bash
python crawl/drive_contracts_to_postgres.py --nhom "BẢO HIỂM"   # chạy thử 1 nhóm nhỏ trước
```

Lần chạy **đầu tiên** (chưa có `token.json`), script tự mở trình duyệt để bạn đăng nhập Google + xác nhận quyền — xong thì `code/token.json` được **tự sinh ra**, các lần chạy sau không cần đăng nhập lại (trừ khi token hết hạn/bị xoá). Chạy xong, terminal in ra dạng:

```
Tìm thấy 246 hợp đồng.
Đã đổ vào Postgres: 246 hợp đồng mới, 0 đã tồn tại (trùng drive_file_id, chỉ update).
```

Nếu output báo `⚠ ... nhánh không truy cập được` — nghĩa là có shortcut Drive hỏng hoặc thiếu quyền; dùng `python crawl/check_broken_shortcuts.py` để liệt kê lại danh sách này bất cứ lúc nào (không ghi DB) sau khi đã xin cấp quyền lại.

Chạy thử ổn thì cào toàn bộ (bỏ `--nhom` để chạy hết mọi nhóm):

```bash
python crawl/drive_contracts_to_postgres.py
```

Script **idempotent** — chạy lại không nhân đôi dữ liệu (khớp theo `drive_file_id`, dòng đã có thì update thay vì insert).

### Bước 7 — Kiểm tra dữ liệu đã vào Postgres

```bash
psql -d contracts_db -c "SELECT nhom, COUNT(*) FROM contracts GROUP BY nhom;"
```

Thấy số liệu theo từng nhóm là Pass 1 đã xong, sẵn sàng cho Pass 2 (OCR bóc mã hợp đồng — xem mục "Cách sử dụng" bên dưới).

## Cách sử dụng

**Pass 2 — OCR bóc mã hợp đồng** (chạy sau khi Pass 1 đã có dữ liệu):
```bash
python ocr/extract_contract_codes.py --limit 30                     # chạy mẫu
python ocr/extract_contract_codes.py                                # chạy hết
python ocr/extract_contract_codes.py --nhom "KHU CÔNG NGHIỆP"       # chỉ 1 nhóm
python ocr/extract_contract_codes.py --ocr-workers 4 --fetch-threads 3   # tinh chỉnh song song theo máy
python ocr/extract_contract_codes.py --rescan no_code               # quét lại thêm dòng không ra mã
python ocr/extract_contract_codes.py --rescan low                   # quét lại rộng nhất (cả dòng đã có mã nhưng chưa chắc)
```
Mặc định chỉ quét các dòng **chưa từng quét** (`--rescan unscanned`). 3 mức `--rescan` lồng nhau, mức sau bao luôn mức trước — dùng `no_code`/`low` sau khi đã sửa regex/tune OCR để quét lại có chọn lọc, đỡ tốn OCR lặp vô ích trên các dòng đã có kết luận.

`--nhom` (cả 2 pass) so khớp kiểu `ILIKE`, không cần gõ y hệt tên. Không truyền `--nhom`/`--limit` → chạy toàn bộ.

**Công cụ phụ trợ (không thuộc luồng chính):**
```bash
python ocr/test_pdf.py                              # OCR thử mọi PDF trong ocr/test_input/, chỉ in ra terminal
python ocr/test_pdf.py duong_dan/file1.pdf           # hoặc chỉ định file/thư mục cụ thể

python ocr/bench_ocr.py cache --n 20                 # tải + cache N PDF thật để benchmark
python ocr/bench_ocr.py run --config baseline        # chạy 1 cấu hình OCR, ghi kết quả JSON
python ocr/bench_ocr.py sweep                        # chạy hết các cấu hình đã định nghĩa rồi so sánh
python ocr/bench_ocr.py compare                      # chỉ in bảng so sánh từ kết quả đã có
```
`test_pdf.py` không đụng Postgres/Drive — dùng để debug OCR trên PDF sẵn có trên máy. `bench_ocr.py` dùng khi cần thử nghiệm/so sánh tham số OCR (mkldnn, dpi, det_limit_side_len...), không cần cho việc chạy pipeline thường ngày.

## Database schema

Bảng `contracts` (tự tạo khi chạy Pass 1 lần đầu):

| Cột | Ý nghĩa |
|---|---|
| `id` | khoá chính |
| `nhom` | Nhóm (folder cấp 1), đã strip prefix "Nhóm:" |
| `brand` | Brand (folder cấp 2), đã strip prefix `[PRT]`/`[CLIENT]`... |
| `brand_raw` | tên folder brand gốc, chưa strip, để đối chiếu |
| `ten_hop_dong` | tên file PDF |
| `ngay_ky` | ngày ký, parse từ tên file |
| `drive_file_id` | ID file trên Drive, **UNIQUE** — khoá idempotent xuyên suốt cả 2 pass |
| `file_path` | đường dẫn đầy đủ trong cây Drive |
| `extracted_at` | thời điểm Pass 1 ghi dòng này |
| `contract_code` | mã hợp đồng bóc từ nội dung PDF (Pass 2) |
| `contract_code_source` | nguồn bóc mã: `pdf_text` \| `ocr_tier1` \| `pdf_text+ocr_tier1` \| `attached_parent` |
| `contract_code_confidence` | `NULL` = chưa từng quét · `low` = đã quét nhưng không chắc/không ra mã (cần soi tay) · `high` = đã xong |
| `dinh_kem_hop_dong_so` | có giá trị khi văn bản là Phụ lục, mã lấy mượn từ hợp đồng gốc được viện dẫn |

`contract_code IS NULL` **không** đồng nghĩa "chưa quét" — cột phân biệt đúng trạng thái là `contract_code_confidence` (xem bảng trên), vì `extract()` vẫn trả `confidence='low'` ngay cả khi không đọc được mã.

## Xử lý sự cố thường gặp

| Hiện tượng | Nguyên nhân/cách xử lý |
|---|---|
| `RuntimeError: Thiếu biến X trong code/.env` | Chưa tạo `.env` (Bước 5) hoặc thiếu 1 trong 5 biến bắt buộc. |
| Trình duyệt báo "Google chưa xác minh ứng dụng này" lúc đăng nhập | Bình thường với app ở chế độ Testing (Bước 4) — bấm Advanced → Go to (tên app) (unsafe). |
| Đã đăng nhập được nhưng vài ngày sau chạy lại bị bắt đăng nhập tiếp | Refresh token hết hạn sau 7 ngày (giới hạn của chế độ Testing) — đăng nhập lại là xong. |
| Đổi OAuth scope rồi script vẫn lỗi quyền | Xoá `code/token.json` rồi chạy lại để đăng nhập lấy token mới đúng scope. |
| `psycopg2.OperationalError: connection refused` | Postgres chưa chạy, hoặc `PG_HOST`/`PG_PORT` trong `.env` sai. |
| Pass 1 báo `⚠ ... nhánh không truy cập được` | Shortcut Drive hỏng hoặc tài khoản chưa được share thư mục đó — nhờ chủ sở hữu share lại rồi chạy `python crawl/check_broken_shortcuts.py` để xác nhận. |

## Chạy trên Windows / Linux / Mac

Pipeline chạy được trên cả 3 hệ điều hành — đã chọn `fork`/`spawn` theo `sys.platform` cho multiprocessing, và ép UTF-8 cho stdout/stderr + file I/O (không phụ thuộc locale máy). Khác biệt chỉ nằm ở bước **cài đặt** (xem các bước ở trên), không phải lúc chạy — lệnh chạy các script giống hệt nhau trên mọi OS. Riêng Windows thuần (không qua WSL): không cần `libgomp1`, và cần tự cài Postgres for Windows hoặc trỏ `.env` sang Postgres từ xa. Lưu ý: các fix portability này **chưa được kiểm chứng trên máy Windows thật** (môi trường phát triển hiện tại chỉ có WSL2/Linux).

## Việc còn lại

1. Chạy full 4024 dòng (`python ocr/extract_contract_codes.py`), dùng `--rescan low` để tận dụng các fix regex đã vá gần đây trên cả các dòng từng chạy `confidence='low'` trước đó.
2. Soi tay các dòng `confidence='low'` hoặc `contract_code IS NULL` còn lại sau bước 1.
3. Quyết định có cần bật OCR tier 2 (`PaddleOCR-VL`, code đã có nhưng chưa bật) dựa trên tỷ lệ soi tay thực tế.
4. Bắt đầu Pass 3 khi `contract_code` đã phủ đủ.
