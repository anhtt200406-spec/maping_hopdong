"""Pass 2: bóc mã hợp đồng từ nội dung PDF, ghi vào cột contract_code.

Cách chạy (từ code/):
    python ocr/extract_contract_codes.py --limit 30                     # chạy mẫu
    python ocr/extract_contract_codes.py                                # chạy hết
    python ocr/extract_contract_codes.py --nhom "KHU CÔNG NGHIỆP"       # 1 nhóm
    python ocr/extract_contract_codes.py --ocr-workers 4 --fetch-threads 3
    python ocr/extract_contract_codes.py --rescan no_code               # quét lại thêm dòng không ra mã
    python ocr/extract_contract_codes.py --rescan low                   # quét lại rộng nhất
    python ocr/extract_contract_codes.py --rescan all                   # quét lại TẤT CẢ, kể cả đã xong

Mặc định chỉ quét dòng CHƯA từng quét (--rescan unscanned). 4 mức rescan lồng
nhau, mức sau bao mức trước (xem postgres_store.py) - dùng no_code/low sau
khi sửa regex/tune OCR để quét lại có chọn lọc, đỡ tốn OCR lặp vô ích. `all`
tốn nhiều OCR nhất, chỉ dùng khi cần backfill 1 trường mới (vd header_text)
cho các dòng đã xong từ trước.

Chạy song song 2 lớp (xem run_pipeline): thread pool tải PDF (I/O-bound) đẩy
sang process pool chạy OCR (CPU-bound, PaddleOCR không nhả GIL nên phải
multiprocessing chứ không threading). Mặc định 4 OCR worker = số core VẬT LÝ
của máy này (8 nproc chỉ là hyperthread) - đã đo thực tế, 6+ worker không lợi thêm.
"""

import argparse
import collections
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool

import psycopg2

# ocr/ nằm trong code/, cần thêm code/ vào sys.path để import được các
# module dùng chung (config, drive_auth, postgres_store) nằm ở gốc code/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code_extractor import extract
from config import PG_CONFIG
from drive_auth import load_credentials
from pdf_fetcher import fetch_pdf_bytes, get_thread_service
from postgres_store import count_status, fetch_pending, update_contract_code_cur


def _ocr_task(pdf_bytes):
    """Chạy trong 1 OCR worker process (fork/spawn tuỳ OS, xem run_pipeline).
    extract() tự lazy-init PaddleOCR model ngay trong process này - process
    cha tuyệt đối không được gọi extract() trước khi tạo pool, kẻo fork mang
    theo model dở dang, không an toàn giữa các process."""
    t0 = time.perf_counter()
    try:
        code, source, confidence, dinh_kem, header_text = extract(pdf_bytes)
    except Exception as e:
        return None, "extract_error", None, None, None, time.perf_counter() - t0, repr(e)
    return code, source, confidence, dinh_kem, header_text, time.perf_counter() - t0, None


def _fetch_task(creds, drive_file_id):
    """Chạy trong 1 fetch thread - mỗi thread tự có Drive service riêng
    (get_thread_service), httplib2.Http bên trong không thread-safe."""
    service = get_thread_service(creds)
    t0 = time.perf_counter()
    pdf_bytes = fetch_pdf_bytes(service, drive_file_id)
    return pdf_bytes, time.perf_counter() - t0


def run_pipeline(rows, creds, ocr_workers, fetch_threads):
    """Điều phối tải (thread pool) + OCR (process pool) song song, giới hạn số
    file "đang dở" cùng lúc để RAM không phình. Ghi DB + in tiến độ ở 1 chỗ
    (process chính) để khỏi chia sẻ connection Postgres giữa các worker."""
    # Ghim cpu_threads=1 trước khi fork/spawn - không thì N worker song song
    # sẽ tranh CPU lẫn nhau (paddleocr mặc định 10 thread/instance).
    os.environ["OCR_CPU_THREADS"] = "1"
    # "fork" không tồn tại trên Windows -> chọn theo platform: giữ "fork"
    # (nhanh) trên Linux/WSL/Mac, "spawn" trên Windows (chỉ chậm hơn lúc khởi
    # động worker, không ảnh hưởng throughput ổn định sau đó).
    ctx = mp.get_context("spawn" if sys.platform == "win32" else "fork")
    window = ocr_workers * 2 + fetch_threads

    conn = psycopg2.connect(**PG_CONFIG)
    cur = conn.cursor()
    counts = collections.Counter()
    low_confidence = []
    tong_tai, tong_ocr = 0.0, 0.0
    n_done = 0
    rows_iter = iter(rows)
    fetch_futs, ocr_futs = {}, {}
    outstanding = 0

    def make_pool():
        return ProcessPoolExecutor(max_workers=ocr_workers, mp_context=ctx)

    ocr_pool = make_pool()

    with ThreadPoolExecutor(max_workers=fetch_threads) as fetch_pool:

        def fill():
            nonlocal outstanding
            while outstanding < window:
                row = next(rows_iter, None)
                if row is None:
                    return
                drive_file_id, _file_path = row
                fut = fetch_pool.submit(_fetch_task, creds, drive_file_id)
                fetch_futs[fut] = row
                outstanding += 1

        def finalize(row, code, source, confidence, dinh_kem, header_text, t_tai, t_ocr):
            nonlocal n_done, outstanding, tong_tai, tong_ocr
            drive_file_id, file_path = row
            update_contract_code_cur(cur, drive_file_id, code, source, confidence, dinh_kem, header_text)
            conn.commit()
            tong_tai += t_tai
            tong_ocr += t_ocr
            n_done += 1
            counts[source if code else "none"] += 1
            ghi_chu = " (mã mượn từ hợp đồng nguyên tắc)" if source == "attached_parent" else ""
            print(f"  [{source or '-'}/{confidence or '-'}] (tải {t_tai:.1f}s, xử lý {t_ocr:.1f}s) "
                  f"{file_path} -> {code or '???'}{ghi_chu}")
            if confidence == "low" or code is None:
                low_confidence.append((file_path, code))
            outstanding -= 1
            fill()

        fill()
        while fetch_futs or ocr_futs:
            done, _ = wait(set(fetch_futs) | set(ocr_futs), return_when=FIRST_COMPLETED)
            for fut in done:
                if fut in fetch_futs:
                    row = fetch_futs.pop(fut)
                    try:
                        pdf_bytes, t_tai = fut.result()
                    except Exception:
                        finalize(row, None, "fetch_error", None, None, None, 0.0, 0.0)
                        continue
                    ocr_fut = ocr_pool.submit(_ocr_task, pdf_bytes)
                    ocr_futs[ocr_fut] = (row, t_tai)
                else:
                    row, t_tai = ocr_futs.pop(fut)
                    try:
                        code, source, confidence, dinh_kem, header_text, t_ocr, _err = fut.result()
                    except BrokenProcessPool:
                        print("  !! OCR worker process crash - khởi động lại pool, "
                              "đánh dấu các file đang xử lý dở là lỗi để chạy lại sau.")
                        for f2, (row2, _t) in list(ocr_futs.items()):
                            finalize(row2, None, "worker_crashed", None, None, None, 0.0, 0.0)
                            ocr_futs.pop(f2, None)
                        ocr_pool.shutdown(wait=False, cancel_futures=True)
                        ocr_pool = make_pool()
                        finalize(row, None, "worker_crashed", None, None, None, t_tai, 0.0)
                        continue
                    finalize(row, code, source, confidence, dinh_kem, header_text, t_tai, t_ocr)

    ocr_pool.shutdown(wait=True)
    cur.close()
    conn.close()
    return counts, low_confidence, tong_tai, tong_ocr, n_done


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Chỉ xử lý N dòng đầu (chạy mẫu). Bỏ qua để chạy hết.",
    )
    parser.add_argument(
        "--nhom",
        help='Chỉ xử lý 1 nhóm cụ thể, ví dụ "KHU CÔNG NGHIỆP". Bỏ qua để chạy tất cả nhóm.',
    )
    parser.add_argument(
        "--ocr-workers",
        type=int,
        default=4,
        help="Số process OCR chạy song song (mặc định 4 = số core VẬT LÝ của máy này - "
             "8 'nproc' chỉ là hyperthread của 4 core thật (lscpu), đã đo thực tế 4 worker "
             "cho throughput tốt nhất, 6+ chỉ gây tranh CPU không lợi thêm).",
    )
    parser.add_argument(
        "--fetch-threads",
        type=int,
        default=3,
        help="Số thread tải PDF từ Drive chạy song song (mặc định 3, tải chỉ ~4s/file nên không cần nhiều).",
    )
    parser.add_argument(
        "--rescan",
        choices=["unscanned", "no_code", "low", "all"],
        default="unscanned",
        help="Mức quét lại, LỒNG NHAU (mức sau bao luôn mức trước) - mặc định "
             "'unscanned' (chỉ chưa-từng-quét/bị crash). 'no_code' quét lại thêm "
             "các dòng đã quét nhưng không ra mã nào. 'low' quét lại thêm cả các "
             "dòng đã có mã nhưng chưa chắc. 'all' quét lại TẤT CẢ kể cả dòng đã "
             "xong (confidence cao) - tốn OCR nhiều nhất, chỉ dùng để backfill 1 "
             "trường mới. Xem docstring đầu file.",
    )
    args = parser.parse_args()

    total, chua_quet, low_khong_ma, low_co_ma = count_status(nhom=args.nhom)
    da_xong = total - chua_quet - low_khong_ma - low_co_ma
    pham_vi = f'nhóm "{args.nhom}"' if args.nhom else "toàn bộ"
    se_quet = {
        "unscanned": chua_quet,
        "no_code": chua_quet + low_khong_ma,
        "low": chua_quet + low_khong_ma + low_co_ma,
        "all": total,
    }[args.rescan]
    print(f"Phạm vi {pham_vi}: {total} hợp đồng | {chua_quet} chưa từng quét | "
          f"{low_khong_ma} đã quét không ra mã | {low_co_ma} đã quét có mã nhưng chưa chắc | "
          f"{da_xong} đã xong (confidence cao) || --rescan={args.rescan} sẽ xử lý {se_quet} dòng.")

    creds = load_credentials()
    rows = fetch_pending(limit=args.limit, nhom=args.nhom, rescan=args.rescan)
    print(f"Sẽ xử lý {len(rows)} hợp đồng trong lượt chạy này "
          f"({args.ocr_workers} OCR worker, {args.fetch_threads} fetch thread).")

    t_start = time.perf_counter()
    counts, low_confidence, tong_tai, tong_ocr, n_done = run_pipeline(
        rows, creds, args.ocr_workers, args.fetch_threads
    )
    wall = time.perf_counter() - t_start

    # In danh sách "cần soi tay" trước, tóm tắt sau cùng - danh sách này có
    # thể dài hàng trăm dòng, in trước sẽ bị cuộn khuất mất khi lệnh chạy xong.
    if low_confidence:
        print(f"\n⚠ {len(low_confidence)} hợp đồng cần soi tay (confidence thấp hoặc không đọc được):")
        for file_path, code in low_confidence:
            print(f"   {file_path} -> {code or '(không đọc được)'}")

    n = n_done or 1
    print(f"\nThời gian trung bình/file: tải {tong_tai / n:.1f}s | xử lý (text-layer/OCR) {tong_ocr / n:.1f}s | "
          f"tổng {(tong_tai + tong_ocr) / n:.1f}s | wall-clock thực tế {wall / n:.1f}s/file "
          f"(tổng {wall:.0f}s cho {n_done} file, chạy song song)")
    print(f"Tổng xử lý: {n_done} | " +
          " | ".join(f"{k}: {v}" for k, v in counts.items()))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
