"""tạo bảng và upsert rows vào Postgres."""

import psycopg2
from psycopg2.extras import execute_values

from config import PG_CONFIG

DDL = """
CREATE TABLE IF NOT EXISTS contracts (
    id            SERIAL PRIMARY KEY,
    nhom          TEXT NOT NULL,
    brand         TEXT NOT NULL,
    brand_raw     TEXT,
    ten_hop_dong  TEXT NOT NULL,
    ngay_ky       DATE,
    drive_file_id TEXT UNIQUE,
    file_path     TEXT,
    extracted_at  TIMESTAMP DEFAULT now()
);

-- Pass 2: mã hợp đồng bóc từ nội dung PDF (text layer hoặc OCR)
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS contract_code TEXT;
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS contract_code_source TEXT;
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS contract_code_confidence TEXT;
-- Chỉ có giá trị khi contract_code là mã MƯỢN từ hợp đồng nguyên tắc khác
-- (văn bản này không có Số: riêng, vd Phụ lục đính kèm - xem code_extractor.py)
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS dinh_kem_hop_dong_so TEXT;
-- Bỏ raw_header_text: JSON thô không dùng để tra cứu được gì, chỉ tốn chỗ.
ALTER TABLE contracts DROP COLUMN IF EXISTS raw_header_text;
-- Text vùng header đã đọc để tìm mã (cả trang 1 nếu PDF gốc, vùng crop nếu
-- phải OCR) - phục vụ mapping/tra cứu ở Pass 3 (khác raw_header_text ở trên:
-- lần này là TEXT thô tra cứu được bằng ILIKE, không phải JSON debug).
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS header_text TEXT;
"""

# 4 mức "cần quét lại", LỒNG NHAU (unscanned ⊂ no_code ⊂ low ⊂ all):
#   1. unscanned: chưa từng quét / quét bị crash - confidence NULL thật.
#   2. no_code: (1) + đã quét nhưng không ra mã nào (confidence='low', code NULL).
#   3. low: (2) + đã quét, có mã nhưng cross-check ngày không khớp.
#   4. all: TẤT CẢ, kể cả confidence='high' - tốn OCR nhiều nhất, chỉ dùng để
#      backfill 1 trường mới (vd header_text) cho các dòng đã xong từ trước.
# Mặc định chỉ quét mức 1 - rẻ nhất, không tốn OCR lặp lại kết luận đã có.
FETCH_PENDING_UNSCANNED = """
SELECT drive_file_id, file_path
FROM contracts
WHERE contract_code_confidence IS NULL
"""

FETCH_PENDING_NO_CODE = """
SELECT drive_file_id, file_path
FROM contracts
WHERE contract_code IS NULL
"""

FETCH_PENDING_LOW = """
SELECT drive_file_id, file_path
FROM contracts
WHERE (contract_code_confidence IS NULL OR contract_code_confidence = 'low')
"""

# WHERE true bắt buộc phải có - fetch_pending() luôn nối thêm "AND nhom ILIKE
# %s" phía sau nếu có --nhom, thiếu WHERE ở đây sẽ vỡ SQL.
FETCH_PENDING_ALL = """
SELECT drive_file_id, file_path
FROM contracts
WHERE true
"""

_FETCH_PENDING_BY_RESCAN = {
    "unscanned": FETCH_PENDING_UNSCANNED,
    "no_code": FETCH_PENDING_NO_CODE,
    "low": FETCH_PENDING_LOW,
    "all": FETCH_PENDING_ALL,
}

COUNT_STATUS = """
SELECT COUNT(*),
       COUNT(*) FILTER (WHERE contract_code_confidence IS NULL),
       COUNT(*) FILTER (WHERE contract_code_confidence = 'low' AND contract_code IS NULL),
       COUNT(*) FILTER (WHERE contract_code_confidence = 'low' AND contract_code IS NOT NULL)
FROM contracts
"""

UPDATE_CONTRACT_CODE = """
UPDATE contracts
SET contract_code = %s,
    contract_code_source = %s,
    contract_code_confidence = %s,
    dinh_kem_hop_dong_so = %s,
    header_text = %s
WHERE drive_file_id = %s;
"""

UPSERT = """
INSERT INTO contracts
    (nhom, brand, brand_raw, ten_hop_dong, ngay_ky, drive_file_id, file_path)
VALUES %s
ON CONFLICT (drive_file_id) DO UPDATE SET
    nhom         = EXCLUDED.nhom,
    brand        = EXCLUDED.brand,
    brand_raw    = EXCLUDED.brand_raw,
    ten_hop_dong = EXCLUDED.ten_hop_dong,
    ngay_ky      = EXCLUDED.ngay_ky,
    file_path    = EXCLUDED.file_path
RETURNING (xmax = 0) AS inserted;
"""


def ensure_schema():
    """Chạy DDL (tạo bảng + thêm cột nếu thiếu). Gọi trước khi đọc/ghi vì
    Pass 2 có thể chạy độc lập, chưa chắc save() (Pass 1) đã từng chạy để
    tạo cột mới."""
    conn = psycopg2.connect(**PG_CONFIG)
    with conn, conn.cursor() as cur:
        cur.execute(DDL)
    conn.close()


def save(rows):
    """Upsert rows vào Postgres. Trả về (so_moi, so_da_ton_tai):
    xmax = 0 nghĩa là dòng vừa được INSERT mới; khác 0 nghĩa là dòng cũ bị
    UPDATE do đụng ON CONFLICT (drive_file_id) -> đếm được số hợp đồng mới
    thật sự mà không cần query đếm bảng trước/sau."""
    if not rows:
        return 0, 0
    conn = psycopg2.connect(**PG_CONFIG)
    with conn, conn.cursor() as cur:
        cur.execute(DDL)
        results = execute_values(cur, UPSERT, rows, fetch=True)
    conn.close()
    inserted = sum(1 for (is_new,) in results if is_new)
    updated = len(results) - inserted
    return inserted, updated


def fetch_pending(limit=None, nhom=None, rescan="unscanned"):
    """Lấy các dòng cần quét (drive_file_id, file_path).
    nhom: lọc theo nhóm (ILIKE, không cần gõ y hệt), None = tất cả.
    rescan: 1 trong 3 mức lồng nhau, xem comment FETCH_PENDING_* phía trên."""
    ensure_schema()
    query, params = _FETCH_PENDING_BY_RESCAN[rescan], []
    if nhom is not None:
        query += " AND nhom ILIKE %s"
        params.append(f"%{nhom}%")
    query += " ORDER BY id"
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    conn = psycopg2.connect(**PG_CONFIG)
    with conn, conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    conn.close()
    return rows


def count_status(nhom=None):
    """Đếm theo nhom hoặc toàn bộ. Trả về (tổng, chưa_quét, low_không_mã,
    low_có_mã) - "đã xong" (confidence=high) = tổng trừ 3 số này, không đếm riêng."""
    ensure_schema()
    query, params = COUNT_STATUS, []
    if nhom is not None:
        query += " WHERE nhom ILIKE %s"
        params.append(f"%{nhom}%")
    conn = psycopg2.connect(**PG_CONFIG)
    with conn, conn.cursor() as cur:
        cur.execute(query, params)
        total, chua_quet, low_khong_ma, low_co_ma = cur.fetchone()
    conn.close()
    return total, chua_quet, low_khong_ma, low_co_ma


def update_contract_code(drive_file_id, code, source, confidence, dinh_kem_hop_dong_so=None, header_text=None):
    """Ghi kết quả bóc mã hợp đồng vào dòng tương ứng (khớp theo drive_file_id)."""
    conn = psycopg2.connect(**PG_CONFIG)
    with conn, conn.cursor() as cur:
        cur.execute(UPDATE_CONTRACT_CODE, (code, source, confidence, dinh_kem_hop_dong_so, header_text, drive_file_id))
    conn.close()


def update_contract_code_cur(cur, drive_file_id, code, source, confidence, dinh_kem_hop_dong_so=None, header_text=None):
    """Giống update_contract_code() nhưng dùng cursor đã mở sẵn (1 connection
    tái sử dụng suốt lượt chạy) thay vì connect() mới mỗi dòng - dùng khi chạy
    pipeline song song (extract_contract_codes.py::run_pipeline), gọi commit()
    ở phía caller sau mỗi lần gọi hàm này."""
    cur.execute(UPDATE_CONTRACT_CODE, (code, source, confidence, dinh_kem_hop_dong_so, header_text, drive_file_id))
