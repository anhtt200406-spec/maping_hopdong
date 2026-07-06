# Dự án: Danh mục hợp đồng từ Google Drive → Postgres

> Trạng thái: **Pass 1 ĐÃ CHẠY XONG — 4024 hợp đồng trong Postgres**
> Cập nhật lần cuối: 2026-07-01
> Người thực hiện: Thế Anh

---

## 1. Mục tiêu

Xây dựng một **danh mục (catalog) hợp đồng** có thể tra cứu được, bằng cách trích xuất thông tin từ cây thư mục Google Drive của bộ phận Legal và đổ vào Postgres local.

Mỗi hợp đồng cần 3 trường lõi:

| Trường | Nguồn | Ví dụ |
|---|---|---|
| `nhom` (nhóm lĩnh vực) | Tên folder cấp 1 | `NGÂN HÀNG - TÀI CHÍNH - CHỨNG KHOÁN` |
| `brand` | Tên folder cấp 2 | `ADTECHNOLOGY & SNST` |
| `ten_hop_dong` | Tên file PDF | `[CLIENT] HĐNT - ADTECHNOLOGY & SNST VIỆT NAM - 18.12.2025.pdf` |

**Mục tiêu xa hơn:** danh mục này là bước đệm cho bài toán *mapping mã hợp đồng ↔ record* (xem mục 2), nên schema được thiết kế sẵn để mở rộng.

---

## 2. Bài toán gốc (bối cảnh sinh ra dự án)

Vấn đề ban đầu: **file PDF định danh bằng "mã hợp đồng", còn cơ sở dữ liệu định danh bằng "tên"** → cần nối hai bên lại.

Bản chất là bài toán **matching giữa hai tập dữ liệu không cùng khóa định danh**. Mấu chốt không phải "đánh dấu", mà là **tìm được khóa chung (join key)** để nối. Có 3 tình huống:

- **TH1 — PDF chứa cả mã lẫn tên** (phổ biến nhất): bóc cả hai từ PDF → có cặp `(mã, tên)` → join `tên` với DB → suy ra `mã ↔ record`.
- **TH2 — PDF chỉ có mã, DB chỉ có tên, không gì chung**: bắt buộc phải có bảng tra cứu trung gian `mã ↔ tên` từ nguồn khác. Không có thì không thể tự động map.
- **TH3 — Match qua thuộc tính phụ**: nối qua MST / ngày ký / số tiền nếu cả mã lẫn tên đều không nối được.

→ Thực tế rơi vào **TH1**: trong PDF hợp đồng có in cả tên bên A/nhà cung cấp lẫn số hợp đồng (ví dụ `1812/2025/HDNT/URBOX-ADTECH`).

**Ba nhóm output của mọi bài matching** (cần chia rõ từ đầu):
1. Khớp 1-1 chắc chắn (exact) → auto-confirm.
2. Khớp mờ / nhiều ứng viên (fuzzy) → đẩy ra review tay.
3. Không khớp (PDF không có record, hoặc record không có file).

---

## 3. Nguồn dữ liệu

Google Drive, mục **"Shared with me"**, folder gốc: `2. [CLIENT]- TỔNG HỢP...` (owner: Legal UrBox).

**Cấu trúc cây 3 cấp:**

```
2. [CLIENT]- TỔNG HỢP.../         <- root
├── Nhóm: NGÂN HÀNG...            <- CẤP 1 = nhóm
│   ├── ADTECHNOLOGY & SNST       <- CẤP 2 = brand
│   │   └── [CLIENT] HĐNT - ... - 18.12.2025.pdf   <- CẤP 3 = file hợp đồng
│   ├── 3M
│   └── ACECOOK
├── Nhóm: BẢO HIỂM
├── Nhóm: HÀNG HÓA BÁN LẺ
├── Nhóm: KHU CÔNG NGHIỆP
├── Nhóm: TRUYỀN THÔNG- THÔNG TIN- CÔNG NGHỆ
├── Nhóm: VẬN TẢI - DẦU KHÍ
└── LĨNH VỰC KHÁC
```

**Nhận định then chốt:** cả 3 trường cần lấy đều **nằm sẵn trong đường dẫn (path)** → *không cần mở/parse nội dung PDF* cho Pass 1. Đây bản chất là bài **duyệt cây thư mục**, không phải bóc tách PDF. Kích thước file (16MB+) không liên quan ở bước này.

---

## 4. Research: hai hướng tiếp cận

### Hướng A — Google Drive API (ĐÃ CHỌN)
Duyệt cây folder qua API, chỉ lấy **tên + ID**, **không tải file**.
- ✅ Nhanh, nhẹ, không tốn ổ đĩa (chỉ đọc metadata).
- ✅ Chạy vài chục giây → 1-2 phút cho vài trăm brand.
- ⚠️ Phải setup OAuth một lần.

### Hướng B — Sync Drive for Desktop rồi `os.walk`
Sync toàn bộ file về máy, dùng `os.walk`/`pathlib`.
- ✅ Code cực đơn giản.
- ❌ Phải tải toàn bộ file (tốn dung lượng + thời gian nếu folder lớn).
- Chỉ hợp lý nếu *đằng nào cũng* cần file local.

### Quyết định
**Chọn Hướng A** vì mục tiêu Pass 1 chỉ cần *tên* → không có lý do tải hàng GB PDF.

### Postgres local có hợp lý không?
Khách quan: với 3 cột text và vài trăm–vài nghìn dòng thì Postgres hơi nặng tay (CSV/SQLite là đủ). **Nhưng vẫn chọn Postgres** vì bảng này là *phía tên* của bài mapping mã (mục 2); sau này thêm cột `contract_code` là ghép được. Nếu chỉ tra cứu một lần thì dùng nhẹ hơn.

---

## 5. Database schema

```sql
CREATE TABLE contracts (
    id            SERIAL PRIMARY KEY,
    nhom          TEXT NOT NULL,      -- group (đã strip "Nhóm:")
    brand         TEXT NOT NULL,      -- brand (đã strip [PRT]/[CLIENT]...)
    brand_raw     TEXT,              -- tên folder gốc, để đối chiếu
    ten_hop_dong  TEXT NOT NULL,      -- tên file .pdf
    ngay_ky       DATE,              -- parse từ tên file (18.12.2025)
    drive_file_id TEXT UNIQUE,       -- ID Drive: idempotent + cầu nối sang Pass 2
    file_path     TEXT,              -- full path để audit
    extracted_at  TIMESTAMP DEFAULT now()
    -- contract_code TEXT            -- (Pass 2) mã bóc từ nội dung PDF
);
```

**Vì sao có `drive_file_id UNIQUE`:** để chạy lại script nhiều lần không nhân đôi (`INSERT ... ON CONFLICT (drive_file_id) DO UPDATE`). ID này ổn định kể cả khi rename file, và chính là khóa để Pass 2 tải đúng file về bóc mã.

> **Lưu ý về bảng mapping riêng:** khi làm bài nối mã ↔ record, KHÔNG sửa thẳng record gốc. Tạo bảng `contract_file_map(record_id, contract_code, file_path, match_method, confidence, status)` để vừa nối được vừa audit được (biết vì sao nối, độ tin cậy bao nhiêu). Khi `confirmed` hết mới UPDATE ngược vào bảng chính.

---

## 6. Các "bẫy" dữ liệu (phát hiện từ ảnh thực tế)

1. **Tên nhóm không đồng nhất:** `Nhóm: BẢO HIỂM` có prefix nhưng `LĨNH VỰC KHÁC` thì không → strip `"Nhóm:"` có điều kiện.
2. **Brand có prefix:** `[PRT] SUBE`, `[PRT] NGUYỄN KIM` → tách/giữ ở `brand_raw`.
3. **Tên file có prefix + ngày:** `[CLIENT] HĐNT - ... - 18.12.2025.pdf` → regex bắt ngày `\d{2}\.\d{2}\.\d{4}`.
4. **Shortcut, không phải folder:** vài item (vd `AEGIS MEDIA`, `AEON VIỆT NAM`) là *shortcut* (`mimeType = ...shortcut`), trỏ tới đối tượng thật qua `shortcutDetails.targetId`. Nếu lọc thẳng `mimeType == folder` sẽ **bỏ sót** → phải resolve shortcut trước.
5. **Phân trang:** `files.list` mặc định trả tối đa 100 (max 1000), phần còn lại sau `nextPageToken`. Không lặp token → mất dữ liệu âm thầm.
6. **Độ sâu không cố định:** brand thường có subfolder ("2024", "Phụ lục"...) → viết đệ quy, gom PDF ở *bất kỳ đâu* dưới brand thay vì cứng "đúng 3 tầng". PDF nằm cao bất thường (`len(path) < 3`) → gắn cờ soi tay.

---

## 7. Kế hoạch triển khai

### Pass 1 — Lập danh mục tên (SẴN SÀNG)
Script: `drive_contracts_to_postgres.py`

1. Setup Drive API + OAuth (mục 8), tải `credentials.json`.
2. Lấy `ROOT_FOLDER_ID` từ URL Drive.
3. Duyệt đệ quy: list con → resolve shortcut → phân trang → gom PDF.
4. Chuẩn hóa: `clean_nhom`, `clean_brand`, `parse_date`.
5. Upsert vào Postgres với `ON CONFLICT (drive_file_id)`.
6. In danh sách PDF sai cấu trúc để kiểm tra tay.

### Pass 2 — Bóc mã hợp đồng (KẾ HOẠCH)
1. Nâng scope OAuth lên `drive.readonly` (để tải được file, không chỉ metadata).
2. `SELECT drive_file_id FROM contracts WHERE contract_code IS NULL`.
3. Tải từng PDF về (theo `drive_file_id`).
4. Bóc text trang đầu (`pdfplumber`/`PyMuPDF`; nếu PDF scan → OCR `pytesseract`/`ocrmypdf`).
5. Regex bắt mã: dạng `1812/2025/HDNT/URBOX-ADTECH`.
6. `UPDATE contracts SET contract_code = ...`.
   → *Không phải quét lại Drive từ đầu.*

### Pass 3 (nếu cần) — Nối mã ↔ record DB khác
Theo mô hình 3 nhóm output (mục 2): exact join trên tên đã chuẩn hóa trước, phần dư mới fuzzy (`rapidfuzz`), giữ `confidence` + `status`.

---

## 8. Ghi chú kỹ thuật: OAuth hoạt động thế nào (để hiểu, không chỉ làm theo)

**Bài toán:** làm sao cho script đọc Drive mà KHÔNG đưa mật khẩu Google (đưa mật khẩu = trao toàn quyền, không thu hồi được).

**Giải pháp OAuth 2.0** tách 2 thứ mật khẩu gộp làm một:
- *Authentication:* bạn **là ai**.
- *Authorization:* bạn cho **một app cụ thể** làm **một việc cụ thể** trên dữ liệu của bạn.

Ẩn dụ **chìa valet:** không đưa chìa master; đưa chìa chỉ lái được, không mở cốp, đòi lại lúc nào cũng được. Script = anh valet.

**Ba vai:** Chủ dữ liệu (bạn) — App/client (script) — Google (giữ dữ liệu + trọng tài cấp phép).

**Các khái niệm trong Phần 1:**

| Khái niệm | Bản chất |
|---|---|
| **OAuth consent screen** | Khai sinh + đăng ký danh tính cho app (Google không phân biệt "app triệu user" với "script cá nhân" — mọi thứ gọi API đều phải là app đăng ký). Lúc chạy, chính là màn hình "App X muốn truy cập Drive — Cho phép/Từ chối" = khoảnh khắc chủ dữ liệu ủy quyền. |
| **Test users** | App chưa được Google verify → bị chặn người lạ. Thêm email mình vào để tự cho phép dùng app chưa-duyệt của mình. |
| **OAuth client ID + `credentials.json`** | Thẻ căn cước của **app** (client_id = định danh công khai, client_secret = "mật khẩu app"). Trả lời "app này *là ai*?". Tĩnh, gắn với ứng dụng, CHƯA cho quyền gì. |
| **Application type = Desktop app** | Quyết định cách Google trả *authorization code* về. Web app có server; Desktop app không → dùng mẹo **loopback**: `flow.run_local_server()` bật web server tí hon trên `localhost` để hứng code. "Public client" — client_secret không thực sự bí mật (nằm trên máy), và điều đó không sao vì an ninh đến từ *sự đồng ý ở consent screen* + code chỉ giao về đúng localhost của bạn. |
| **`token.json`** (sinh lúc chạy) | Bằng chứng bạn ĐÃ cho phép. Chứa *access token* (~1h) + *refresh token* (tự xin vé mới). Trả lời "người dùng đã *cho phép* app chưa?". Động, gắn với bạn+app. Xóa nó = "quên việc đã cho phép" → lần sau phải đăng nhập lại. |
| **Scope** (`drive.metadata.readonly`) | Ranh giới chiếc chìa valet — ghi trong token rằng app *chỉ* được đọc metadata, không tải file, không đụng Gmail. |

**Hai file, hai vai trò:**
- `credentials.json` → "App này *là ai*?" — tĩnh, gắn với ứng dụng, chưa có quyền.
- `token.json` → "Đã được *cho phép* chưa?" — động, là bằng chứng quyền thật.

**Bẫy "7 ngày":** app ở chế độ Testing → Google cố tình cho refresh token hết hạn sau 7 ngày (van an toàn với app chưa verify). Không phải bug; với extract một lần thì không ảnh hưởng.

---

## 9. Checklist setup (một lần)

- [x] Tạo project trên Google Cloud Console
- [x] Bật Google Drive API
- [x] Cấu hình OAuth consent screen (User type = External)
- [x] Thêm email mình vào **Test users**
- [x] Tạo OAuth client ID → **Desktop app** → tải `credentials.json`
- [x] `pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib psycopg2-binary`
- [x] Điền `ROOT_FOLDER_ID` (từ URL) + `PG_CONFIG` trong script
- [x] Chạy lần đầu → đăng nhập trên trình duyệt → sinh `token.json`

---

## 10. Câu hỏi mở / cần quyết

- [ ] Dừng ở Pass 1 (danh mục tên) hay đi tiếp Pass 2 (bóc mã)?
- [ ] Có nguồn "sổ hợp đồng" / Excel quản lý nào chứa sẵn `mã ↔ tên` không (tránh phải OCR)?
- [ ] PDF là text hay scan ảnh (quyết định có cần OCR ở Pass 2)?
- [ ] Có brand/nhóm nào cấu trúc lệch chuẩn (nhiều tầng con) cần xử lý riêng?

---

## 11. Nhật ký chạy thật — 2026-07-01

### Môi trường đã dựng
- Postgres 18 cài local trong WSL (trước đó máy chỉ có `postgresql-client`, phải `sudo apt-get install postgresql`).
- DB `contracts_db`, user `postgres`, **password hiện tại: `postgres123`** (đổi từ `hopdong_local_2026` ban đầu để loại trừ lỗi gõ nhầm khi test pgAdmin — nhớ đổi lại `PG_CONFIG` trong script nếu đổi password lần nữa).
- Venv sẵn ở `.venv/`, đã cài đủ package (mục 9).
- `credentials.json` + `token.json` đã có trong `code/` — tài khoản Google đã cấp quyền: **`intern.data5@urbox.vn`** (không phải gmail cá nhân).
- `ROOT_FOLDER_ID = 1ynUBKYVyyO1-cL2djQZDFiAk3WHIPeXH`.

### Kết quả Pass 1
- **4024 hợp đồng** đã upsert vào bảng `contracts`.
- Phân bố theo nhóm: LĨNH VỰC KHÁC 1973, NGÂN HÀNG-TC-CK 616, TRUYỀN THÔNG-CN 465, HÀNG HÓA BÁN LẺ 378, VẬN TẢI-DẦU KHÍ 263, BẢO HIỂM 246, KHU CÔNG NGHIỆP 83.

### Bug tìm thấy khi review kỹ (đã sửa trong `drive_contracts_to_postgres.py`)
**Mất dữ liệu âm thầm khi shortcut trỏ tới file/folder không có quyền truy cập.** `files.list(q="'<id>' in parents")` của Google **không báo lỗi** nếu `<id>` không truy cập được — chỉ trả về danh sách rỗng, nên code cũ tưởng nhầm là "folder trống" thay vì "không đọc được". Còn 1 shortcut trỏ thẳng tới PDF (không phải folder) từng bị ghi nhầm thành 1 dòng hợp lệ trong DB dù file đó không mở được (brand HSBC, đã xoá dòng rác này khỏi bảng).

**Fix:** thêm `_shortcut_target_accessible()` — gọi `files.get(fileId=target_id)` để xác nhận tồn tại/quyền truy cập *trước khi* coi shortcut là hợp lệ; nếu lỗi (404/403) thì ghi vào danh sách `errors` riêng thay vì bỏ qua âm thầm hoặc thêm nhầm vào `rows`.

### 18 nhánh Drive không truy cập được (cần Legal share lại trực tiếp, không sửa được bằng code)
Tài khoản `intern.data5@urbox.vn` thấy shortcut nhưng không mở được target (404 — shortcut "link chết", có thể do người tạo share nhầm chỉ share shortcut chứ không share file/folder gốc):

- VẬN TẢI-DẦU KHÍ: VIETNAM AIRLINES, BAMBOO, BE GROUP
- NGÂN HÀNG-TC-CK: VP BANK, MBBANK, HSBC
- LĨNH VỰC KHÁC: DELOITTE, OPPO-VĨNH KHANG, FUNTAP, IAI, FINCORP, VMFLOUR, WIZELINE, FPTUGlobal
- HÀNG HÓA BÁN LẺ: ONEID-VINID
- TRUYỀN THÔNG-CN: VIETTEL, ITEL-ĐÔNG DƯƠNG TELECOM (2 shortcut trùng 1 target)

→ Việc cần làm: nhờ Legal UrBox share trực tiếp các file/folder đích ở trên cho `intern.data5@urbox.vn`, rồi chạy lại script (idempotent, an toàn chạy nhiều lần).

### 7 PDF sai cấu trúc (nằm thẳng dưới Nhóm, không qua Brand) — cần soi tay gán brand
`Nhóm: HÀNG HÓA BÁN LẺ`: MEGAMED, IMD, LAMY, IPP, SAGOPHAR, GOLDENLIFE, ORGALIFE.

### Cách xem dữ liệu bằng pgAdmin 4 (chạy trên Windows, Postgres nằm trong WSL2)
Server mới → tab Connection:
- Host: `localhost` (WSL2 tự forward; nếu không vào được, thử IP WSL hiện tại qua lệnh `hostname -I`, IP đổi mỗi lần khởi động lại WSL)
- Port: `5432`
- Maintenance database: `contracts_db`
- Username: `postgres`
- Password: `postgres123` (gõ tay, đừng copy-paste — lỗi "password authentication failed" từng gặp là do gõ/dán sai, không phải lỗi cấu hình Postgres)
pass: hopdong_local_2026
