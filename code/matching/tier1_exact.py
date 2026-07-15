"""Tier 1: exact match contract_code (Drive, đã OCR) <-> contract_code
(ct_contracts, field DB gốc urcard-portal), sau chuẩn hoá.

Chạy được ngay, không cần VPN/Playwright - toàn bộ dữ liệu cần đã có sẵn
trong contracts_db. Phụ lục bị loại khỏi vòng exact match theo đúng chỉ đạo
nghiệp vụ (xem normalize.is_addendum), luôn rơi vào tập "chưa khớp" cho Tier 2.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # code/

import pandas as pd

import store
from normalize import is_addendum, normalize_code

_DRIVE_COLS = [
    "drive_file_id", "file_path", "contract_code",
    "contract_code_confidence", "dinh_kem_hop_dong_so", "header_text",
]
_CT_COLS = ["ct_code", "contract_code_urcard", "contract_path"]


def run_tier1():
    """Ghi các dòng khớp exact vào contract_mapping (match_method='exact_code').
    Trả về (n_matched, unmatched_drive_file_ids, unmatched_ct_codes) - 2 tập
    "chưa khớp" này dùng chung cho mục 5 (fetch có chọn lọc) và Tier 2, tránh
    mỗi bước tự query lại DB rồi lệch state (đúng thiết kế trong plan)."""
    # pandas 3.x's mặc định dtype "str" biến None (từ psycopg2/SQL NULL) thành
    # NaN (float) thay vì giữ None - mà NaN trong Python là truthy, sẽ làm
    # is_addendum()/normalize_code() (viết cho None) chạy sai âm thầm (không
    # raise lỗi). Ép về object + where(...) để trả lại None thật cho mọi ô rỗng.
    drive_df = pd.DataFrame(store.read_drive_rows(), columns=_DRIVE_COLS).astype(object)
    drive_df = drive_df.where(pd.notnull(drive_df), None)
    ct_df = pd.DataFrame(store.read_ct_contracts(), columns=_CT_COLS).astype(object)
    ct_df = ct_df.where(pd.notnull(ct_df), None)

    drive_df["code_norm"] = drive_df.apply(
        lambda r: None
        if is_addendum(r["contract_code"], r["dinh_kem_hop_dong_so"])
        else normalize_code(r["contract_code"]),
        axis=1,
    )
    ct_df["code_norm"] = ct_df["contract_code_urcard"].apply(normalize_code)

    drive_candidates = drive_df[drive_df["code_norm"].notna()]
    ct_candidates = ct_df[ct_df["code_norm"].notna()]

    merged = drive_candidates.merge(ct_candidates, on="code_norm", how="inner")

    # Chính sách mã trùng (plan mục 7): 1 code_norm khớp nhiều drive_file_id
    # -> ép manual_review, không bao giờ tự auto_match khi có trùng.
    dup_mask = merged.duplicated(subset="code_norm", keep=False)
    merged = merged.assign(
        review_status=pd.Series("auto_match", index=merged.index).mask(dup_mask, "manual_review")
    )

    mapping_rows = [
        (
            row.ct_code,
            row.drive_file_id,
            row.file_path,
            row.contract_code,
            row.contract_code_urcard,
            "exact_code",
            1.0,
            row.review_status,
        )
        for row in merged.itertuples()
    ]
    store.insert_mapping_rows(mapping_rows)

    matched_drive_ids = set(merged["drive_file_id"])
    matched_ct_codes = set(merged["ct_code"])
    unmatched_drive_ids = set(drive_df["drive_file_id"]) - matched_drive_ids
    unmatched_ct_codes = set(ct_df["ct_code"]) - matched_ct_codes

    return len(merged), unmatched_drive_ids, unmatched_ct_codes


if __name__ == "__main__":
    store.truncate_mapping()
    n_matched, unmatched_drive, unmatched_ct = run_tier1()
    print(f"Tier 1 (exact_code): {n_matched} dòng khớp.")
    print(f"Còn chưa khớp: {len(unmatched_drive)} dòng Drive, {len(unmatched_ct)} dòng ct_contracts.")
