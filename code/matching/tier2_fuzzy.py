"""Tier 2: fuzzy text match (30% header_text bên Drive <-> 30% header_text
đã OCR từ urcard-portal) cho phần CHƯA khớp Tier 1 (chủ yếu phụ lục, đúng
chỉ đạo nghiệp vụ - xem normalize.is_addendum). Chạy SAU KHI
fetch_ct_contracts.py + backfill_header_text.py đã có header_text ở cả 2
phía - dòng nào chưa có/quá ngắn sẽ tự rơi vào no_match (không bịa kết quả).

Ngưỡng 3 vùng lấy đúng từ phase2.md mục 2 (đã chốt, thiên conservative vì
stakes tài chính): >=0.85 auto_match | 0.60-0.85 manual_review | <0.60 no_match.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # code/

from rapidfuzz import fuzz, process

import store

_MIN_TEXT_LEN = 50
_AUTO_MATCH_THRESHOLD = 0.85
_MANUAL_REVIEW_THRESHOLD = 0.60

_DRIVE_COLS = [
    "drive_file_id", "file_path", "contract_code",
    "contract_code_confidence", "dinh_kem_hop_dong_so", "header_text",
]


def _review_status(score_0_1):
    if score_0_1 >= _AUTO_MATCH_THRESHOLD:
        return "auto_match"
    if score_0_1 >= _MANUAL_REVIEW_THRESHOLD:
        return "manual_review"
    return "no_match"


def run_tier2(unmatched_drive_ids, unmatched_ct_codes):
    """Ghi kết quả vào contract_mapping (match_method='fuzzy_text'). MỌI
    ct_code trong unmatched_ct_codes đều ra đúng 1 dòng kết quả, kể cả
    no_match - giữ đúng tinh thần "ct_code dẫn dắt", không bỏ sót dòng nào."""
    drive_by_id = {r[0]: r for r in store.read_drive_rows()}

    candidates = []  # (drive_file_id, file_path, contract_code, header_text)
    for did in unmatched_drive_ids:
        row = drive_by_id.get(did)
        if not row:
            continue
        header_text = row[5]
        if header_text and len(header_text) >= _MIN_TEXT_LEN:
            candidates.append((row[0], row[1], row[2], header_text))
    candidate_texts = [c[3] for c in candidates]

    fetch_results = store.read_fetch_results(unmatched_ct_codes)

    mapping_rows = []
    for ct_code in unmatched_ct_codes:
        header_text, contract_code_ocr, _fetch_status = fetch_results.get(ct_code, (None, None, None))

        if not header_text or len(header_text) < _MIN_TEXT_LEN or not candidate_texts:
            mapping_rows.append(
                (ct_code, None, None, None, contract_code_ocr, "fuzzy_text", None, "no_match")
            )
            continue

        best = process.extractOne(header_text, candidate_texts, scorer=fuzz.token_sort_ratio)
        if best is None:
            mapping_rows.append(
                (ct_code, None, None, None, contract_code_ocr, "fuzzy_text", None, "no_match")
            )
            continue

        _matched_text, score, idx = best
        score_0_1 = round(score / 100.0, 3)
        drive_file_id, file_path, contract_code_drive, _ = candidates[idx]
        mapping_rows.append(
            (
                ct_code, drive_file_id, file_path, contract_code_drive, contract_code_ocr,
                "fuzzy_text", score_0_1, _review_status(score_0_1),
            )
        )

    store.insert_mapping_rows(mapping_rows)
    return mapping_rows
