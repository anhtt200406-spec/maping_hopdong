"""CLI gộp: Tier 1 (exact match - luôn chạy được ngay, không cần VPN/Playwright
vì dữ liệu đã có sẵn trong contracts_db) rồi Tier 2 (fuzzy text - CẦN
header_text đã có ở cả 2 phía, xem fetch_ct_contracts.py + backfill_header_text.py,
nếu chưa có thì phần lớn sẽ rơi vào no_match).

Chạy: python run_matching.py            # chỉ Tier 1
      python run_matching.py --tier2    # Tier 1 rồi Tier 2
"""

import argparse
import collections

import store
from tier1_exact import run_tier1
from tier2_fuzzy import run_tier2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tier2",
        action="store_true",
        help="Chạy thêm Tier 2 (fuzzy) sau Tier 1 - cần header_text đã sẵn sàng ở cả 2 phía "
        "(chạy fetch_ct_contracts.py + backfill_header_text.py trước).",
    )
    args = parser.parse_args()

    # contract_mapping là output rebuildable (matching rẻ, không phải OCR) -
    # truncate 1 lần đầu, cả 2 tier chỉ INSERT thêm.
    store.truncate_mapping()

    n_matched, unmatched_drive, unmatched_ct = run_tier1()
    print(f"Tier 1 (exact_code): {n_matched} dòng khớp.")
    print(f"Còn chưa khớp: {len(unmatched_drive)} dòng Drive, {len(unmatched_ct)} dòng ct_contracts.")

    if args.tier2:
        rows = run_tier2(unmatched_drive, unmatched_ct)
        counts = collections.Counter(r[-1] for r in rows)
        print(f"\nTier 2 (fuzzy_text): {len(rows)} dòng ghi kết quả.")
        for status in ("auto_match", "manual_review", "no_match"):
            print(f"  {status}: {counts.get(status, 0)}")


if __name__ == "__main__":
    main()
