"""Kiểm tra kết nối tới urcard-portal TRƯỚC khi chạy batch fetch (mục 5 plan)
- KHÔNG tự động bật VPN (OpenVPN bật tay theo quyết định của chủ dự án),
chỉ phân biệt rõ nguyên nhân fail để người chạy biết cần làm gì tiếp:
lỗi mạng (chưa bật VPN) khác hẳn session hết hạn (cần đăng nhập lại).
"""

import sys

from playwright.sync_api import sync_playwright

from urcard_config import STORAGE_STATE_PATH, URCARD_BASE_URL

_TIMEOUT_MS = 8000


def check_reachable(base_url=URCARD_BASE_URL, storage_state_path=STORAGE_STATE_PATH):
    """Trả về (ok: bool, reason: str)."""
    if not storage_state_path.exists():
        return False, f"Chưa có {storage_state_path} - chạy `python urcard_auth.py` trước."

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=str(storage_state_path))
            page = context.new_page()
            try:
                response = page.goto(base_url, timeout=_TIMEOUT_MS, wait_until="domcontentloaded")
            except Exception as e:
                browser.close()
                return False, (
                    f"Không kết nối được tới {base_url} ({e}) - kiểm tra đã bật OpenVPN chưa "
                    "(script KHÔNG tự bật VPN, chỉ báo lỗi)."
                )
            final_url = page.url
            browser.close()
    except Exception as e:
        return False, f"Lỗi khởi động Playwright: {e}"

    if "accounts.google.com" in final_url or "/login" in final_url.lower():
        return False, (
            f"Bị redirect ra trang login ({final_url}) - session đã hết hạn, "
            "chạy lại `python urcard_auth.py` để đăng nhập lại."
        )
    if response is not None and response.status >= 400:
        return False, f"{base_url} trả về HTTP {response.status}."
    return True, "OK"


if __name__ == "__main__":
    ok, reason = check_reachable()
    print(("OK: " if ok else "LỖI: ") + reason)
    sys.exit(0 if ok else 1)
