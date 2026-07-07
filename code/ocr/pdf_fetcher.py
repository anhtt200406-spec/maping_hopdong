"""Tải nội dung PDF từ Drive vào RAM (không ghi file tạm ra đĩa)."""

import io

from googleapiclient.http import MediaIoBaseDownload

from drive_auth import execute_with_retry


def fetch_pdf_bytes(service, drive_file_id):
    """Trả về toàn bộ nội dung file dưới dạng bytes. Không ghi gì ra đĩa -
    xử lý xong (OCR/parse) rồi bytes bị garbage-collect, không đọng file rác."""
    request = service.files().get_media(fileId=drive_file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = execute_with_retry(downloader.next_chunk)
    return buf.getvalue()
