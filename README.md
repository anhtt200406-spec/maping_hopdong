# Danh mục hợp đồng — Google Drive → Postgres

Trích xuất danh mục hợp đồng (nhóm/brand/tên file) từ cây thư mục Google Drive (Legal) đổ vào Postgres, sau đó dùng OCR để bóc **mã hợp đồng** từ nội dung PDF. Đây là bước đệm cho bài toán xa hơn: *mapping mã hợp đồng ↔ record ở một DB khác* (định danh theo tên, không theo mã).

> Muốn hiểu sâu quyết định kiến trúc, các bug thực tế đã vá, số liệu benchmark, lịch sử thay đổi → xem [`Claude.md`](Claude.md) (nhật ký phát triển chi tiết). File này chỉ là tổng quan + hướng dẫn dùng.

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
├── crawl/          # Pass 1
│   ├── drive_walker.py                  # duyệt đệ quy cây Drive
│   ├── normalize.py                     # chuẩn hoá tên nhóm/brand
│   ├── drive_contracts_to_postgres.py   # entry point Pass 1
│   └── check_broken_shortcuts.py        # chỉ kiểm tra nhánh Drive lỗi quyền, không ghi DB
└── ocr/            # Pass 2
    ├── pdf_fetcher.py                   # tải PDF từ Drive
    ├── header_ocr.py                    # crop + chạy PaddleOCR
    ├── code_extractor.py                # regex bóc mã + cross-check
    ├── extract_contract_codes.py        # entry point Pass 2 (song song hoá)
    ├── test_pdf.py                      # debug OCR 1 file, không đụng Drive/Postgres
    └── bench_ocr.py                     # benchmark cấu hình OCR (công cụ phụ trợ)
```
Không dùng package/`__init__.py` — mỗi entry point tự thêm `code/` vào `sys.path` lúc chạy.

## Yêu cầu hệ thống

- **Python 3.12** — đây là bản duy nhất đã test và xác nhận chạy được. **Không dùng Python 3.14** (`paddlepaddle` từng không có wheel hỗ trợ, đã xác nhận thực tế). Các bản 3.9–3.11/3.13 chưa được test trong dự án này.
- **Postgres** (bảng `contracts`, xem schema bên dưới).
- **Tài khoản Google** có quyền truy cập thư mục Drive chứa hợp đồng, cộng Google Cloud project đã bật Drive API (để lấy `credentials.json`).
- **Linux/WSL:** cần thêm gói hệ thống `libgomp1` cho `paddlepaddle` (`sudo apt-get install libgomp1`). **Windows:** không cần bước này — wheel `paddlepaddle` cho Windows tự đóng gói runtime.

## Cài đặt

Từ thư mục `code/`:

```bash
# 1. Tạo & kích hoạt venv (Python 3.12)
python3.12 -m venv .venv          # Windows: py -3.12 -m venv .venv
source .venv/bin/activate         # Windows PowerShell: .venv\Scripts\Activate.ps1

# 2. Cài thư viện
pip install -r requirements.txt

# 3. Cấu hình .env
cp .env.example .env
# rồi điền: ROOT_FOLDER_ID, PG_DBNAME, PG_USER, PG_PASSWORD, PG_HOST, PG_PORT
```

**OAuth Google Drive** (chỉ cần làm 1 lần): tạo Google Cloud project → bật Drive API → tạo OAuth consent screen (External, thêm Test users) → tạo OAuth client loại *Desktop app* → tải về lưu thành `code/credentials.json`. Scope hiện dùng là `drive.readonly`. Lần chạy đầu tiên, script sẽ tự mở trình duyệt để đăng nhập và sinh ra `code/token.json` — không cần tạo file này tay. App ở chế độ Testing nên refresh token tự hết hạn sau 7 ngày, lúc đó chỉ cần đăng nhập lại.

**Postgres:** tạo database + bảng theo schema bên dưới (script Pass 1 tự tạo bảng nếu chưa có), rồi trỏ đúng `PG_HOST`/`PG_PORT`/`PG_DBNAME`/`PG_USER`/`PG_PASSWORD` trong `.env`.

## Cách sử dụng

Tất cả lệnh chạy từ thư mục `code/`.

**Pass 1 — cào danh mục từ Drive vào Postgres:**
```bash
python crawl/drive_contracts_to_postgres.py                    # toàn bộ
python crawl/drive_contracts_to_postgres.py --nhom "BẢO HIỂM"  # chỉ 1 nhóm (test nhanh)
```

**Kiểm tra nhánh Drive không truy cập được** (không ghi DB, chỉ để tái kiểm tra sau khi nhờ Legal share lại):
```bash
python crawl/check_broken_shortcuts.py
```

**Pass 2 — OCR bóc mã hợp đồng:**
```bash
python ocr/extract_contract_codes.py --limit 30                     # chạy mẫu
python ocr/extract_contract_codes.py                                # chạy hết
python ocr/extract_contract_codes.py --nhom "KHU CÔNG NGHIỆP"       # chỉ 1 nhóm
python ocr/extract_contract_codes.py --ocr-workers 4 --fetch-threads 3   # tinh chỉnh song song theo máy
python ocr/extract_contract_codes.py --rescan no_code               # quét lại thêm dòng không ra mã
python ocr/extract_contract_codes.py --rescan low                   # quét lại rộng nhất (cả dòng đã có mã nhưng chưa chắc)
```
Mặc định chỉ quét các dòng **chưa từng quét** (`--rescan unscanned`). 3 mức `--rescan` lồng nhau, mức sau bao luôn mức trước — dùng `no_code`/`low` sau khi đã sửa regex/tune OCR để quét lại có chọn lọc, đỡ tốn OCR lặp vô ích trên các dòng đã có kết luận.

`--nhom` (cả 2 pass) so khớp kiểu `ILIKE`, không cần gõ y hệt tên. Không truyền `--nhom`/`--limit` → chạy toàn bộ. Cả 2 pass đều **idempotent** — chạy lại không nhân đôi dữ liệu, tự báo trước đã có sẵn bao nhiêu / còn thiếu bao nhiêu.

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

## Chạy trên Windows / Linux / Mac

Pipeline chạy được trên cả 3 hệ điều hành — đã audit portability (chọn `fork`/`spawn` theo `sys.platform` cho multiprocessing) và ép UTF-8 cho stdout/stderr + file I/O (không phụ thuộc locale máy). Khác biệt chỉ nằm ở bước **cài đặt**, không phải lúc chạy — lệnh chạy các script giống hệt nhau trên mọi OS (xem mục "Cách sử dụng"). Riêng Windows thuần (không qua WSL): không cần `libgomp1`, và cần tự cài Postgres for Windows hoặc trỏ `.env` sang Postgres từ xa (môi trường dev hiện dùng Postgres trong WSL2). Chi tiết audit portability xem `Claude.md` mục 7 — lưu ý các fix này **chưa được kiểm chứng trên máy Windows thật**.

## Việc còn lại

1. Chạy full 4024 dòng (`python ocr/extract_contract_codes.py`), dùng `--rescan low` để tận dụng các fix regex đã vá gần đây trên cả các dòng từng chạy `confidence='low'` trước đó.
2. Soi tay các dòng `confidence='low'` hoặc `contract_code IS NULL` còn lại sau bước 1.
3. Quyết định có cần bật OCR tier 2 (`PaddleOCR-VL`, code đã có nhưng chưa bật) dựa trên tỷ lệ soi tay thực tế.
4. Bắt đầu Pass 3 khi `contract_code` đã phủ đủ.

Chi tiết đầy đủ (câu hỏi mở, các quyết định đã cân nhắc và loại trừ...) xem mục 9-10 trong [`Claude.md`](Claude.md).
