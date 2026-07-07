"""Pass 2: bóc mã hợp đồng từ nội dung PDF, ghi vào cột contract_code.

Chạy mẫu trước khi chạy full (từ code/):
    python ocr/extract_contract_codes.py --limit 30
Chạy hết:
    python ocr/extract_contract_codes.py
Chỉ 1 nhóm cụ thể (giống --nhom bên crawl/), có thể kèm --limit để test mẫu:
    python ocr/extract_contract_codes.py --nhom "KHU CÔNG NGHIỆP"
"""

import argparse
import collections
import os
import sys

# ocr/ nằm trong code/, cần thêm code/ vào sys.path để import được các
# module dùng chung (config, drive_auth, postgres_store) nằm ở gốc code/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code_extractor import extract
from drive_auth import get_service
from pdf_fetcher import fetch_pdf_bytes
from postgres_store import count_status, fetch_pending, update_contract_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Chỉ xử lý N dòng đầu (chạy mẫu). Bỏ qua để chạy hết.",
    )
    parser.add_argument(
        "--nhom",
        help='Chỉ xử lý 1 nhóm cụ thể, ví dụ "KHU CÔNG NGHIỆP". Bỏ qua để chạy tất cả nhóm.',
    )
    args = parser.parse_args()

    total, done = count_status(nhom=args.nhom)
    pham_vi = f'nhóm "{args.nhom}"' if args.nhom else "toàn bộ"
    print(f"Phạm vi {pham_vi}: {total} hợp đồng, {done} đã có contract_code từ trước (bỏ qua), "
          f"{total - done} còn thiếu.")

    service = get_service()
    rows = fetch_pending(limit=args.limit, nhom=args.nhom)
    print(f"Sẽ xử lý {len(rows)} hợp đồng trong lượt chạy này.")

    counts = collections.Counter()
    low_confidence = []

    for drive_file_id, file_path in rows:
        pdf_bytes = fetch_pdf_bytes(service, drive_file_id)
        code, source, confidence, dinh_kem = extract(pdf_bytes)
        update_contract_code(drive_file_id, code, source, confidence, dinh_kem)

        counts[source if code else "none"] += 1
        ghi_chu = " (mã mượn từ hợp đồng nguyên tắc)" if source == "attached_parent" else ""
        print(f"  [{source or '-'}/{confidence or '-'}] {file_path} -> {code or '???'}{ghi_chu}")
        if confidence == "low" or code is None:
            low_confidence.append((file_path, code))

    print(f"\nTổng xử lý: {len(rows)} | " +
          " | ".join(f"{k}: {v}" for k, v in counts.items()))

    if low_confidence:
        print(f"\n⚠ {len(low_confidence)} hợp đồng cần soi tay (confidence thấp hoặc không đọc được):")
        for file_path, code in low_confidence:
            print(f"   {file_path} -> {code or '(không đọc được)'}")


if __name__ == "__main__":
    main()
