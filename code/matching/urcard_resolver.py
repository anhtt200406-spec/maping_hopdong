"""Resolve contract_path (link urcard-portal) -> bytes file thật, tải vào
RAM, KHÔNG ghi đĩa - cùng tinh thần "tải vào BytesIO" của pdf_fetcher.py bên
Pass 2, dù không import được file đó (cơ chế khác hẳn: Drive API vs Playwright).

Dùng Playwright ASYNC API (khác urcard_auth.py/urcard_preflight.py dùng sync
API cho đơn giản vì đó là script chạy 1 lần, không cần concurrency) - để
fetch_ct_contracts.py chạy được nhiều resolve() song song trong 1 event loop
qua asyncio.Semaphore, không cần mở nhiều browser process song song.

CHƯA xác nhận bằng DevTools cơ chế redirect thật (302 thuần hay JS xử lý,
xem phase2.md mục 3.2 + plan mục 3) - viết phòng thủ cả 2 khả năng. Khi chạy
thật lần đầu (cần VPN), nên mở DevTools Network tab quan sát xem nhánh nào
thực sự chạy, có thể đơn giản hoá lại resolve_contract_pdf() sau khi biết chắc.
"""

import re

_UPLOAD_HOST = "upload.urbox.vn"
_EXT_RE = re.compile(r"\.([a-zA-Z0-9]{2,5})(?:\?|$)")


class StaleSessionError(Exception):
    """Response bị redirect về trang login giữa chừng - session hết hạn.
    fetch_ct_contracts.py bắt lỗi này để dừng cả batch ngay, thay vì chạy
    tiếp fail hàng loạt vô ích (mỗi dòng đều sẽ fail như nhau)."""


def _guess_ext(url):
    m = _EXT_RE.search(url)
    return m.group(1).lower() if m else None


def _is_login_url(url):
    return "accounts.google.com" in url or "/login" in url.lower()


async def resolve_contract_pdf(context, contract_path, timeout_ms=20000):
    """context: playwright.async_api.BrowserContext (đã nạp storage_state).
    Trả về (bytes, file_ext). Raise StaleSessionError nếu bị đá về login,
    RuntimeError nếu tải file thật thất bại (HTTP lỗi)."""
    final_url = None

    # Fast path: thử xem contract_path có redirect thuần (302) tới host tải
    # file không - nếu đúng thì bỏ qua hẳn bước render trang, nhanh hơn nhiều.
    try:
        probe = await context.request.get(contract_path, timeout=timeout_ms, max_redirects=0)
        if probe.status in (301, 302, 303, 307, 308):
            location = probe.headers.get("location")
            if location and _UPLOAD_HOST in location:
                final_url = location
    except Exception:
        pass

    if final_url is None:
        # Fallback: JS xử lý (không phải redirect thuần) - phải render đầy đủ
        # trang rồi bắt response thật tới host tải file.
        page = await context.new_page()
        try:
            async with page.expect_response(
                lambda r: _UPLOAD_HOST in r.url, timeout=timeout_ms
            ) as resp_info:
                await page.goto(contract_path, timeout=timeout_ms)
            response = await resp_info.value
            final_url = response.url
        finally:
            await page.close()

    if final_url is None or _is_login_url(final_url):
        raise StaleSessionError(
            f"Không resolve được (hoặc bị đá về login) cho contract_path={contract_path!r}, "
            f"final_url={final_url!r}"
        )

    file_resp = await context.request.get(final_url, timeout=timeout_ms)
    if file_resp.status >= 400:
        raise RuntimeError(f"Tải file thất bại (HTTP {file_resp.status}): {final_url}")

    file_ext = _guess_ext(contract_path) or _guess_ext(final_url) or "pdf"
    body = await file_resp.body()
    return body, file_ext
