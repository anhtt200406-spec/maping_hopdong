"""Schema + query riêng cho phase matching (Nguồn B / urcard-portal).

Tự chứa hoàn toàn trong code/matching/ - KHÔNG import/sửa postgres_store.py.
Mọi đọc dữ liệu từ contracts/ct_contracts (2 bảng có sẵn, do crawl/ và ocr/
sở hữu) đều là SELECT thô ngay trong file này, không đụng code cũ.
"""

import os
import sys

# code/matching/store.py -> cần code/ trong sys.path để import config (chứa
# PG_CONFIG dùng chung). Chèn ở đây (không phải từng entrypoint) để bất kỳ
# module nào `import store` cũng tự có code/ trong sys.path, không cần lặp
# lại boilerplate này ở mọi file.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from psycopg2.extras import execute_values

from config import PG_CONFIG

DDL = """
CREATE TABLE IF NOT EXISTS ct_contracts_fetch (
    id                        SERIAL PRIMARY KEY,
    ct_code                   TEXT NOT NULL UNIQUE,
    fetch_status              TEXT,
    file_ext                  TEXT,
    fetch_error               TEXT,
    header_text               TEXT,
    contract_code_ocr         TEXT,
    contract_code_source      TEXT,
    contract_code_confidence  TEXT,
    fetched_at                TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contract_mapping (
    id                    SERIAL PRIMARY KEY,
    ct_code               TEXT NOT NULL,
    drive_file_id         TEXT,
    drive_path            TEXT,
    contract_code_drive   TEXT,
    contract_code_urcard  TEXT,
    match_method          TEXT,
    confidence            NUMERIC(4,3),
    review_status         TEXT,
    reviewed_by           TEXT,
    created_at            TIMESTAMP DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS contract_mapping_ct_drive_uq
    ON contract_mapping (ct_code, drive_file_id);
"""

UPSERT_FETCH = """
INSERT INTO ct_contracts_fetch
    (ct_code, fetch_status, file_ext, fetch_error, header_text,
     contract_code_ocr, contract_code_source, contract_code_confidence, fetched_at)
VALUES %s
ON CONFLICT (ct_code) DO UPDATE SET
    fetch_status             = EXCLUDED.fetch_status,
    file_ext                 = EXCLUDED.file_ext,
    fetch_error               = EXCLUDED.fetch_error,
    header_text               = EXCLUDED.header_text,
    contract_code_ocr         = EXCLUDED.contract_code_ocr,
    contract_code_source      = EXCLUDED.contract_code_source,
    contract_code_confidence  = EXCLUDED.contract_code_confidence,
    fetched_at                = EXCLUDED.fetched_at;
"""

INSERT_MAPPING = """
INSERT INTO contract_mapping
    (ct_code, drive_file_id, drive_path, contract_code_drive, contract_code_urcard,
     match_method, confidence, review_status)
VALUES %s
ON CONFLICT (ct_code, drive_file_id) DO NOTHING;
"""

# ct_contracts_fetch: dòng nào coi là "cần fetch" phụ thuộc --rescan, LỒNG
# NHAU giống 3 mức của postgres_store.py (unfetched ⊂ fetch_error ⊂ all):
#   unfetched: chưa có dòng trong ct_contracts_fetch, hoặc có nhưng status
#              chưa phải 'fetched' (kể cả pending/invalid_path/unreachable).
#   fetch_error: unfetched + đã fetch nhưng lỗi mạng tạm thời (fetch_error).
#   all: fetch lại TẤT CẢ kể cả đã 'fetched' - tốn nhất, chỉ dùng khi cần
#        backfill/OCR lại do sửa logic content_extract.py.
_UNFETCHED_STATUSES = ("'fetch_error'", "'unreachable'")


def ensure_schema():
    conn = psycopg2.connect(**PG_CONFIG)
    with conn, conn.cursor() as cur:
        cur.execute(DDL)
    conn.close()


def read_drive_rows():
    """Toàn bộ dòng contracts cần cho Tier 1/backfill: (drive_file_id,
    file_path, contract_code, contract_code_confidence, dinh_kem_hop_dong_so,
    header_text). Chỉ SELECT, không đụng postgres_store.py/bảng contracts."""
    conn = psycopg2.connect(**PG_CONFIG)
    with conn, conn.cursor() as cur:
        cur.execute("""
            SELECT drive_file_id, file_path, contract_code,
                   contract_code_confidence, dinh_kem_hop_dong_so, header_text
            FROM contracts
            WHERE drive_file_id IS NOT NULL
        """)
        rows = cur.fetchall()
    conn.close()
    return rows


def read_ct_contracts():
    """Toàn bộ dòng ct_contracts (danh sách dẫn dắt): (code, contract_code,
    contract_path). Bảng CSV-import thô, không có cột nào NULL theo dữ liệu
    thật đã kiểm chứng, nhưng vẫn SELECT thẳng không giả định gì thêm."""
    conn = psycopg2.connect(**PG_CONFIG)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT code, contract_code, contract_path FROM ct_contracts")
        rows = cur.fetchall()
    conn.close()
    return rows


def read_drive_missing_header_text(drive_file_ids):
    """Dòng contracts trong danh sách ID cho trước mà header_text còn NULL -
    dùng để tính list rows cần đưa vào extract_contract_codes.run_pipeline()
    khi backfill (chỉ phần Tier 1 không match được, không phải toàn bộ 4024)."""
    if not drive_file_ids:
        return []
    conn = psycopg2.connect(**PG_CONFIG)
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT drive_file_id, file_path
            FROM contracts
            WHERE header_text IS NULL
              AND drive_file_id = ANY(%s)
            """,
            (list(drive_file_ids),),
        )
        rows = cur.fetchall()
    conn.close()
    return rows


def truncate_mapping():
    """contract_mapping là output rebuildable (matching rẻ, không phải OCR) -
    gọi 1 lần đầu run_matching.py rồi cả Tier 1 lẫn Tier 2 chỉ INSERT thêm."""
    ensure_schema()
    conn = psycopg2.connect(**PG_CONFIG)
    with conn, conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE contract_mapping")
    conn.close()


def insert_mapping_rows(rows):
    """rows: list tuple (ct_code, drive_file_id, drive_path, contract_code_drive,
    contract_code_urcard, match_method, confidence, review_status)."""
    if not rows:
        return
    ensure_schema()
    conn = psycopg2.connect(**PG_CONFIG)
    with conn, conn.cursor() as cur:
        execute_values(cur, INSERT_MAPPING, rows)
    conn.close()


def upsert_fetch_rows(rows):
    """rows: list tuple (ct_code, fetch_status, file_ext, fetch_error, header_text,
    contract_code_ocr, contract_code_source, contract_code_confidence, fetched_at)."""
    if not rows:
        return
    ensure_schema()
    conn = psycopg2.connect(**PG_CONFIG)
    with conn, conn.cursor() as cur:
        execute_values(cur, UPSERT_FETCH, rows)
    conn.close()


def read_fetch_results(ct_codes):
    """Kết quả OCR đã lưu ở ct_contracts_fetch cho tập ct_code cho trước ->
    dict ct_code -> (header_text, contract_code_ocr, fetch_status). Dùng ở
    Tier 2 để lấy text/mã đã OCR được từ urcard-portal (không tự OCR lại)."""
    if not ct_codes:
        return {}
    conn = psycopg2.connect(**PG_CONFIG)
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT ct_code, header_text, contract_code_ocr, fetch_status
            FROM ct_contracts_fetch
            WHERE ct_code = ANY(%s)
            """,
            (list(ct_codes),),
        )
        rows = cur.fetchall()
    conn.close()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def fetch_unmatched_ct_contracts(limit=None, rescan="unfetched"):
    """ct_contracts chưa khớp Tier 1 (match_method='exact_code' trong
    contract_mapping) VÀ chưa fetch xong (rescan quyết định "chưa xong"
    nghĩa là gì, xem _UNFETCHED_STATUSES phía trên) -> (code, contract_code,
    contract_path). Đây là tập đúng cần Playwright fetch + OCR (mục 5 plan),
    không fetch tràn lan toàn bộ ct_contracts."""
    ensure_schema()
    query = """
        SELECT c.code, c.contract_code, c.contract_path
        FROM ct_contracts c
        WHERE NOT EXISTS (
            SELECT 1 FROM contract_mapping m
            WHERE m.ct_code = c.code AND m.match_method = 'exact_code'
        )
    """
    if rescan == "unfetched":
        query += """
            AND NOT EXISTS (
                SELECT 1 FROM ct_contracts_fetch f
                WHERE f.ct_code = c.code AND f.fetch_status = 'fetched'
            )
        """
    elif rescan == "fetch_error":
        query += f"""
            AND (
                NOT EXISTS (SELECT 1 FROM ct_contracts_fetch f WHERE f.ct_code = c.code)
                OR EXISTS (
                    SELECT 1 FROM ct_contracts_fetch f
                    WHERE f.ct_code = c.code AND f.fetch_status IN ({', '.join(_UNFETCHED_STATUSES)})
                )
            )
        """
    elif rescan != "all":
        raise ValueError(f"rescan không hợp lệ: {rescan!r} (unfetched|fetch_error|all)")
    query += " ORDER BY c.code"
    params = []
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    conn = psycopg2.connect(**PG_CONFIG)
    with conn, conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    conn.close()
    return rows
