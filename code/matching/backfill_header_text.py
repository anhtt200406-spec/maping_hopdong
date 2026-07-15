"""Backfill header_text phía Drive CHỈ cho tập chưa khớp Tier 1 (không phải
toàn bộ 4024 dòng - header_text gần như trống hoàn toàn vì cột được thêm sau
khi Pass 2 đã chạy xong phần lớn, xem Context trong plan) - tái dùng NGUYÊN
VẸN extract_contract_codes.run_pipeline() (đã luôn ghi header_text sẵn cho
mọi kết quả, kể cả confidence=low), KHÔNG sửa logic OCR/extract nào của Pass 2.

Chạy: python backfill_header_text.py               # tự chạy Tier 1 trước rồi backfill phần chưa khớp
      python backfill_header_text.py --ocr-workers 4 --fetch-threads 3
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # code/
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ocr")
)  # code/ocr/

import store


def backfill(unmatched_drive_ids, ocr_workers=4, fetch_threads=3):
    """unmatched_drive_ids: set/list drive_file_id chưa khớp Tier 1 (từ
    tier1_exact.run_tier1()). Chỉ OCR lại đúng dòng trong tập này mà
    header_text còn NULL - tránh OCR lặp vô ích trên dòng đã có header_text
    từ 1 lần backfill trước (idempotent qua lần chạy lại)."""
    rows = store.read_drive_missing_header_text(unmatched_drive_ids)
    if not rows:
        print("Không có dòng nào cần backfill header_text (đã đủ hoặc tập rỗng).")
        return

    from drive_auth import load_credentials
    from extract_contract_codes import run_pipeline

    creds = load_credentials()
    print(f"Backfill header_text cho {len(rows)} dòng Drive chưa khớp Tier 1...")
    run_pipeline(rows, creds, ocr_workers, fetch_threads)


def main():
    import argparse

    from tier1_exact import run_tier1

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr-workers", type=int, default=4)
    parser.add_argument("--fetch-threads", type=int, default=3)
    args = parser.parse_args()

    _n_matched, unmatched_drive_ids, _unmatched_ct = run_tier1()
    backfill(unmatched_drive_ids, ocr_workers=args.ocr_workers, fetch_threads=args.fetch_threads)


if __name__ == "__main__":
    if sys.platform != "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
