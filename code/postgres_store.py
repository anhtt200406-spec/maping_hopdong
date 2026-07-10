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
"""

# 3 mức "cần quét lại", LỒNG NHAU thật sự (unscanned ⊂ no_code ⊂ low - xem
# fetch_pending()):
#   1. unscanned: chưa từng quét/quét bị crash (extract_error/fetch_error/
#      worker_crashed) - contract_code_confidence NULL thật, chưa có kết luận gì.
#   2. no_code: (1) + đã quét nhưng KHÔNG ra được mã nào - extract() vẫn trả
#      confidence='low' cho case này (xem code_extractor.py cuối extract()),
#      không phải NULL, nhưng contract_code cũng NULL. Tương đương hệt
#      "contract_code IS NULL" (filter gốc trước khi tách mức) vì confidence
#      chỉ NULL hoặc 'low' khi code NULL, không bao giờ 'high' (mọi nhánh trả
#      "high" trong extract() đều đi kèm code khác NULL - đã audit code).
#   3. low: (2) + đã quét, CÓ mã nhưng cross-check ngày không khớp (confidence
#      'low' dù contract_code khác NULL) - filter gốc "contract_code IS NULL"
#      KHÔNG bao giờ bắt được nhóm này vì code đã có giá trị.
# Mặc định chỉ lấy mức 1 (rẻ nhất, không tốn OCR quét lại kết luận đã có).
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

_FETCH_PENDING_BY_RESCAN = {
    "unscanned": FETCH_PENDING_UNSCANNED,
    "no_code": FETCH_PENDING_NO_CODE,
    "low": FETCH_PENDING_LOW,
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
    dinh_kem_hop_dong_so = %s
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
    """Chạy DDL (tạo bảng + thêm cột Pass 2 nếu thiếu). save() đã tự gọi
    trước upsert, nhưng fetch_pending()/update_contract_code() (Pass 2) có
    thể chạy độc lập mà chưa từng chạy save() lần nào sau khi thêm cột mới -
    nên phải tự đảm bảo schema đủ trước khi đọc/ghi."""
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
    nhom=None -> lấy tất cả nhóm; nhom="KHU CÔNG NGHIỆP" -> chỉ nhóm đó
    (so khớp ILIKE, không cần gõ đúng y hệt, giống --nhom bên crawl/).
    limit=None -> lấy hết (trong phạm vi nhom đã lọc).
    rescan: 1 trong 3 mức LỒNG NHAU (xem comment các hằng FETCH_PENDING_* phía
    trên) - "unscanned" (mặc định, rẻ nhất, chỉ chưa-từng-quét/bị crash),
    "no_code" (+ đã quét nhưng không ra mã nào), "low" (+ đã quét có mã nhưng
    chưa chắc - rộng nhất)."""
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
    """Đếm theo nhom hoặc toàn bộ, trả về (tổng, chưa_quet, low_khong_ma,
    low_co_ma) - đúng 4 nhóm rạch ròi theo 3 ranh giới mức rescan (xem comment
    COUNT_STATUS/FETCH_PENDING_* phía trên). "đã xong" (confidence='high') suy
    ra ở phía gọi = tổng - 3 số còn lại, không cần đếm riêng."""
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


def update_contract_code(drive_file_id, code, source, confidence, dinh_kem_hop_dong_so=None):
    """Ghi kết quả bóc mã hợp đồng vào dòng tương ứng (khớp theo drive_file_id)."""
    conn = psycopg2.connect(**PG_CONFIG)
    with conn, conn.cursor() as cur:
        cur.execute(UPDATE_CONTRACT_CODE, (code, source, confidence, dinh_kem_hop_dong_so, drive_file_id))
    conn.close()


def update_contract_code_cur(cur, drive_file_id, code, source, confidence, dinh_kem_hop_dong_so=None):
    """Giống update_contract_code() nhưng dùng cursor đã mở sẵn (1 connection
    tái sử dụng suốt lượt chạy) thay vì connect() mới mỗi dòng - dùng khi chạy
    pipeline song song (extract_contract_codes.py::run_pipeline), gọi commit()
    ở phía caller sau mỗi lần gọi hàm này."""
    cur.execute(UPDATE_CONTRACT_CODE, (code, source, confidence, dinh_kem_hop_dong_so, drive_file_id))
