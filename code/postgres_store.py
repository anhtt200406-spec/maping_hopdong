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
