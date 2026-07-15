"""Orchestrator: fetch (Playwright async, 1 browser context, nhiều resolve()
song song qua asyncio.Semaphore) + OCR (ProcessPoolExecutor, fork-pattern y
hệt extract_contract_codes.py bên Pass 2) cho tập ct_contracts CHƯA khớp
Tier 1 (đọc từ store.fetch_unmatched_ct_contracts() - không fetch tràn lan
toàn bộ 4311 dòng, đúng nguyên tắc đã chốt).

Cách chạy (từ code/matching/, SAU KHI đã chạy urcard_auth.py lấy
storage_state.json + đã bật OpenVPN thủ công):
    python fetch_ct_contracts.py --limit 20                      # chạy mẫu
    python fetch_ct_contracts.py --fetch-concurrency 5 --ocr-workers 4
    python fetch_ct_contracts.py --rescan fetch_error             # quét lại dòng lỗi tạm thời
    python fetch_ct_contracts.py --rescan all                     # fetch lại TẤT CẢ

Tự preflight-check trước khi chạy batch (xem urcard_preflight.py), dừng
ngay nếu chưa đạt (chưa VPN/chưa login) - không tự động bật VPN.
"""

import argparse
import asyncio
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # code/
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ocr")
)  # code/ocr/

from playwright.async_api import async_playwright

import store
from urcard_config import STORAGE_STATE_PATH, URCARD_BASE_URL
from urcard_preflight import check_reachable
from urcard_resolver import StaleSessionError, resolve_contract_pdf

_SUPPORTED_EXTS = {"pdf", "docx", "jpg", "jpeg", "png"}


def _is_invalid_path(contract_path):
    """7 dòng contract_path rỗng đã xác nhận bằng SQL thật (filename= không
    có giá trị, không dẫn tới đâu cả) - đánh dấu lỗi ngay, không gọi mạng."""
    return not contract_path or contract_path.rstrip("/").endswith("filename=")


def _ocr_task(file_bytes, file_ext):
    """Chạy trong 1 OCR worker process (fork). Import trong hàm để KHÔNG
    load PaddleOCR/code_extractor ở process cha - process cha tuyệt đối
    không được gọi extract()/ocr_tier1() trước khi ProcessPoolExecutor tạo
    xong, kẻo fork mang theo model dở dang (ràng buộc y hệt extract_contract_codes.py)."""
    if file_ext == "pdf":
        from code_extractor import extract

        return extract(file_bytes)
    if file_ext == "docx":
        from content_extract import extract_docx

        return extract_docx(file_bytes)
    if file_ext in ("jpg", "jpeg", "png"):
        from content_extract import extract_image

        return extract_image(file_bytes)
    return None, None, "low", None, ""


async def _fetch_and_ocr(context, semaphore, stop_event, loop, ocr_pool, code, contract_path):
    if stop_event.is_set():
        return code, ("skipped_stale_session", None, None, None)

    async with semaphore:
        if stop_event.is_set():
            return code, ("skipped_stale_session", None, None, None)
        try:
            file_bytes, file_ext = await resolve_contract_pdf(context, contract_path)
        except StaleSessionError as e:
            stop_event.set()
            return code, ("fetch_error", None, None, str(e))
        except Exception as e:
            return code, ("fetch_error", None, None, str(e))

    if file_ext not in _SUPPORTED_EXTS:
        return code, ("skipped_non_pdf", file_ext, None, f"đuôi file không hỗ trợ: {file_ext}")

    try:
        ocr_result = await loop.run_in_executor(ocr_pool, _ocr_task, file_bytes, file_ext)
    except Exception as e:
        return code, ("fetch_error", file_ext, None, f"OCR lỗi: {e}")

    return code, ("fetched", file_ext, ocr_result, None)


async def _run_async(rows, fetch_concurrency, ocr_pool):
    results = {}
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    semaphore = asyncio.Semaphore(fetch_concurrency)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=str(STORAGE_STATE_PATH))
        try:
            tasks = [
                _fetch_and_ocr(context, semaphore, stop_event, loop, ocr_pool, code, contract_path)
                for code, _contract_code, contract_path in rows
            ]
            for coro in asyncio.as_completed(tasks):
                code, result = await coro
                results[code] = result
        finally:
            await context.close()
            await browser.close()

    return results, stop_event.is_set()


def run_pipeline(rows, fetch_concurrency=5, ocr_workers=4):
    """rows: list (ct_code, contract_code, contract_path) từ
    store.fetch_unmatched_ct_contracts(). Trả về dict ct_code -> kết quả,
    đồng thời tự ghi vào ct_contracts_fetch (upsert)."""
    invalid_rows = [(c, cc, cp) for c, cc, cp in rows if _is_invalid_path(cp)]
    live_rows = [(c, cc, cp) for c, cc, cp in rows if not _is_invalid_path(cp)]

    fetch_upserts = []
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for code, _cc, _cp in invalid_rows:
        fetch_upserts.append((code, "invalid_path", None, "contract_path rỗng/không hợp lệ", None, None, None, None, now))

    # OCR_CPU_THREADS=1 TRƯỚC khi tạo pool - mỗi worker pin 1 thread, tránh
    # tranh CPU giữa ocr_workers process (y hệt extract_contract_codes.py).
    os.environ["OCR_CPU_THREADS"] = "1"
    ctx = mp.get_context("spawn" if sys.platform == "win32" else "fork")

    if live_rows:
        with ProcessPoolExecutor(max_workers=ocr_workers, mp_context=ctx) as ocr_pool:
            results, stopped_early = asyncio.run(_run_async(live_rows, fetch_concurrency, ocr_pool))
        if stopped_early:
            print(
                "!! Dừng batch sớm: phát hiện session hết hạn (bị đá về trang login). "
                "Chạy lại `python urcard_auth.py` để đăng nhập lại rồi chạy tiếp "
                "(--rescan fetch_error để chỉ quét lại phần dở dang)."
            )

        for code, contract_code, contract_path in live_rows:
            status, file_ext, ocr_result, err = results.get(
                code, ("skipped_stale_session", None, None, None)
            )
            if status == "skipped_stale_session":
                continue  # chưa kịp chạy, giữ nguyên trạng thái cũ, để lần sau --rescan fetch_error
            if status != "fetched":
                fetch_upserts.append((code, status, file_ext, err, None, None, None, None, now))
                continue
            code_ocr, source, confidence, _dinh_kem, header_text = ocr_result
            fetch_upserts.append(
                (code, "fetched", file_ext, None, header_text, code_ocr, source, confidence, now)
            )

    store.upsert_fetch_rows(fetch_upserts)
    return fetch_upserts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--fetch-concurrency", type=int, default=5)
    parser.add_argument("--ocr-workers", type=int, default=4)
    parser.add_argument("--rescan", choices=["unfetched", "fetch_error", "all"], default="unfetched")
    args = parser.parse_args()

    ok, reason = check_reachable()
    if not ok:
        print(f"Preflight thất bại: {reason}")
        sys.exit(1)
    print(f"Preflight OK ({URCARD_BASE_URL}).")

    rows = store.fetch_unmatched_ct_contracts(limit=args.limit, rescan=args.rescan)
    print(f"Sẽ fetch+OCR {len(rows)} dòng ct_contracts (rescan={args.rescan}).")
    if not rows:
        return

    fetch_upserts = run_pipeline(rows, fetch_concurrency=args.fetch_concurrency, ocr_workers=args.ocr_workers)
    n_fetched = sum(1 for r in fetch_upserts if r[1] == "fetched")
    print(f"Xong: {n_fetched}/{len(fetch_upserts)} dòng fetch+OCR thành công.")


if __name__ == "__main__":
    if sys.platform != "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
