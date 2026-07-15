"""Bootstrap 1 lần: mở browser HEADED, chờ đăng nhập Google SSO thủ công,
lưu lại storage_state.json để các lần chạy batch sau (preflight/resolver)
tái sử dụng session này, không cần đăng nhập lại mỗi lần.

Chạy: python urcard_auth.py
Yêu cầu: máy có màn hình/X server thật (browser headed không chạy được trên
server thuần terminal không GUI) + đã bật OpenVPN thủ công trước khi chạy.
Không liên quan/không sửa gì tới drive_auth.py (khác hẳn cơ chế: OAuth token
JSON của Drive API vs browser cookie snapshot của Playwright).
"""

from playwright.sync_api import sync_playwright

from urcard_config import STORAGE_STATE_PATH, URCARD_BASE_URL


def save_storage_state(path=STORAGE_STATE_PATH, base_url=URCARD_BASE_URL):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(base_url)
        print(f"Đã mở {base_url} - tự đăng nhập Google SSO thủ công trên cửa sổ browser vừa mở,")
        print("điều hướng tới khi thấy trang urcard-portal đã đăng nhập xong.")
        input("Xong rồi thì quay lại đây, bấm Enter để lưu session... ")
        context.storage_state(path=str(path))
        browser.close()
    print(f"Đã lưu storage_state vào {path}")


if __name__ == "__main__":
    save_storage_state()
