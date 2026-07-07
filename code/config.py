"""
Cấu hình tập trung: đọc từ code/.env
Tạo code/.env từ code/.env.example rồi điền giá trị thật.
"""

import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


def _required(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Thiếu biến {name} trong code/.env (xem code/.env.example)")
    return value


# Pass 2 cần tải nội dung PDF (bóc mã hợp đồng) -> đổi từ drive.metadata.readonly
# sang drive.readonly. Đổi scope -> token.json cũ hết hiệu lực, phải xoá để đăng
# nhập lại (drive_auth.py tự sinh lại token mới).
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")

ROOT_FOLDER_ID = _required("ROOT_FOLDER_ID")

PG_CONFIG = dict(
    dbname=_required("PG_DBNAME"),
    user=_required("PG_USER"),
    password=_required("PG_PASSWORD"),
    host=_required("PG_HOST"),
    port=os.environ.get("PG_PORT", "5432"),
)

FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
PDF = "application/pdf"
