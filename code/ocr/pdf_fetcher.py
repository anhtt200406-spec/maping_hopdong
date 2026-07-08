"""Tải nội dung PDF từ Drive vào RAM (không ghi file tạm ra đĩa)."""

import io
import threading

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from drive_auth import execute_with_retry

_thread_local = threading.local()


def get_thread_service(creds):
    """httplib2.Http bên trong mỗi Drive service KHÔNG thread-safe -> mỗi
    fetch thread (khi chạy nhiều thread song song) cần 1 service riêng, dùng
    chung 1 Credentials đã refresh sẵn (chỉ đọc, không refresh lại giữa các
    thread) để khỏi phải đăng nhập lại."""
    if not hasattr(_thread_local, "service"):
        _thread_local.service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _thread_local.service


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
