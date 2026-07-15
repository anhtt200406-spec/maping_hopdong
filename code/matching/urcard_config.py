"""Config riêng cho phase matching (urcard-portal). Đọc chung code/.env
(load_dotenv gọi lại lần 2 vô hại - chỉ nạp thêm vào os.environ, không
tạo file mới), KHÔNG sửa code/config.py (dùng chung Pass 1/2, không đụng)."""

import os
from pathlib import Path

from dotenv import load_dotenv

_MATCHING_DIR = Path(__file__).resolve().parent
_CODE_DIR = _MATCHING_DIR.parent

load_dotenv(_CODE_DIR / ".env")

# Đã thấy thật trong contract_path mẫu (ct_contracts) - override qua .env
# nếu urcard-portal đổi domain, không bắt buộc set vì đã có default hợp lý.
URCARD_BASE_URL = os.environ.get("URCARD_BASE_URL", "https://urcard-portal-web.urbox.services")

# storage_state.json chứa cookie/localStorage đăng nhập Google SSO - NHẠY CẢM
# như credentials.json/token.json (xem .gitignore ở gốc repo), tuyệt đối
# không commit.
STORAGE_STATE_PATH = _MATCHING_DIR / "storage_state.json"
