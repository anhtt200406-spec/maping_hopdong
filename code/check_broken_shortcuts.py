"""Khâu KIỂM TRA: chỉ duyệt cây Drive để liệt kê nhánh không truy cập được
(shortcut hỏng/thiếu quyền), không upsert Postgres. Dùng để tái kiểm tra
nhanh sau khi nhờ Legal share lại, không cần chạy full pipeline.

Chạy:
    python check_broken_shortcuts.py
"""

from drive_auth import get_service
from drive_walker import walk
from config import ROOT_FOLDER_ID


def main():
    service = get_service()
    errors = []
    pdf_count = 0
    for _ in walk(service, ROOT_FOLDER_ID, errors=errors):
        pdf_count += 1

    print(f"Duyệt xong: {pdf_count} PDF thấy được, {len(errors)} nhánh lỗi.\n")
    for path, fid, status in errors:
        print(f"    [{status}]", "/".join(path) or "(root)", f"(id={fid})")


if __name__ == "__main__":
    main()
