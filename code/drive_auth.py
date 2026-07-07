"""Xác thực OAuth với Google Drive, trả về service client. Cũng giữ
execute_with_retry ở đây (thay vì crawl/drive_walker.py) vì đây là hạ tầng gọi
Drive API dùng chung cho cả crawl/ (Pass 1) và ocr/ (Pass 2)."""

import os
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import SCOPES, CREDENTIALS_FILE, TOKEN_FILE


def execute_with_retry(call, tries=5):
    """Gọi call() (không tham số) và tự chờ-thử-lại nếu server quá tải. Nhận
    một callable thay vì ép kiểu "request có .execute()" để dùng chung được
    cả cho request.execute (crawl/) lẫn downloader.next_chunk (ocr/)."""
    for i in range(tries):
        try:
            return call()
        except HttpError as e:
            if e.resp.status in (403, 429, 500, 503) and i < tries - 1:
                time.sleep(2 ** i)
                continue
            raise


def get_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("drive", "v3", credentials=creds)
