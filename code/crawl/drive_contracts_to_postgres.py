import argparse
import os
import sys

# crawl/ nằm trong code/, cần thêm code/ vào sys.path để import được các
# module dùng chung (config, drive_auth, postgres_store) nằm ở gốc code/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drive_auth import get_service
from normalize import build_rows
from postgres_store import save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--nhom",
        help='Chỉ cào 1 nhóm cụ thể, ví dụ "BẢO HIỂM". Bỏ qua để cào toàn bộ root.',
    )
    args = parser.parse_args()

    service = get_service()
    rows, flagged, errors = build_rows(service, nhom=args.nhom)
    print(f"Tìm thấy {len(rows)} hợp đồng.")
    if flagged:
        print(f"⚠ {len(flagged)} PDF nằm sai cấu trúc (cần kiểm tra tay):")
        for p, _ in flagged:
            print("   ", "/".join(p))
    if errors:
        print(f"⚠ {len(errors)} nhánh không truy cập được (shortcut hỏng/không có quyền, cần nhờ Legal share lại):")
        for p, fid, status in errors:
            print(f"    [{status}]", "/".join(p) or "(root)", f"(id={fid})")
    inserted, updated = save(rows)
    print(f"Đã đổ vào Postgres: {inserted} hợp đồng mới, {updated} đã tồn tại (trùng drive_file_id, chỉ update).")


if __name__ == "__main__":
    main()



"""
Chạy toàn bộ (từ code/):
    python crawl/drive_contracts_to_postgres.py
Chạy riêng 1 nhóm (test nhanh):
    python crawl/drive_contracts_to_postgres.py --nhom "BẢO HIỂM", có thể thay thế bằng tên nhóm khác.
"""