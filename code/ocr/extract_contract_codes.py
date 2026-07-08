"""Pass 2: bóc mã hợp đồng từ nội dung PDF, ghi vào cột contract_code.

Chạy mẫu trước khi chạy full (từ code/):
    python ocr/extract_contract_codes.py --limit 30
Chạy hết:
    python ocr/extract_contract_codes.py
Chỉ 1 nhóm cụ thể (giống --nhom bên crawl/), có thể kèm --limit để test mẫu:
    python ocr/extract_contract_codes.py --nhom "KHU CÔNG NGHIỆP"

Pipeline chạy song song 2 lớp để không phải chờ tuần tự tải-rồi-OCR từng file
một (xem Claude.md mục "hiệu năng"): 1 thread pool tải PDF từ Drive (I/O-bound)
đẩy kết quả sang 1 process pool chạy OCR (CPU-bound, PaddleOCR không giải
phóng GIL nên phải multiprocessing chứ không phải threading). Mặc định 4 OCR
worker (= số core VẬT LÝ thật của máy này, xem `lscpu`: 8 nproc chỉ là
hyperthread của 4 core) - đã đo thực tế, 6+ worker chỉ gây tranh CPU không lợi
thêm. Tinh chỉnh nếu chạy máy khác:
    python ocr/extract_contract_codes.py --ocr-workers 4 --fetch-threads 3
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
    """Chạy trong worker process riêng (fork). extract() tự lazy-init
    PaddleOCR singleton lần đầu gọi TRONG process này - process cha không
    được gọi extract()/header_ocr trước khi tạo pool, nếu không fork sẽ mang
    theo model đã load dở/không an toàn giữa các process."""
    t0 = time.perf_counter()
    try:
        code, source, confidence, dinh_kem = extract(pdf_bytes)
    except Exception as e:
        return None, "extract_error", None, None, time.perf_counter() - t0, repr(e)
    return code, source, confidence, dinh_kem, time.perf_counter() - t0, None


def _fetch_task(creds, drive_file_id):
    """Chạy trong 1 fetch thread - mỗi thread tự có Drive service riêng
    (get_thread_service), httplib2.Http bên trong không thread-safe."""
    service = get_thread_service(creds)
    t0 = time.perf_counter()
    pdf_bytes = fetch_pdf_bytes(service, drive_file_id)
    return pdf_bytes, time.perf_counter() - t0


def run_pipeline(rows, creds, ocr_workers, fetch_threads):
    """Điều phối tải (thread pool) + OCR (process pool) song song, giới hạn
    số file 'đang dở' cùng lúc (window) để RAM không phình theo tổng số file.
    Ghi DB + in tiến độ từ 1 chỗ duy nhất (process chính) để tránh phải chia
    sẻ connection Postgres giữa các worker process."""
    # paddleocr mặc định cpu_threads=10/instance - phải ghim =1 trước khi
    # fork worker, nếu không N process OCR song song sẽ tranh nhau CPU.
    os.environ["OCR_CPU_THREADS"] = "1"
    ctx = mp.get_context("fork")
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

        def finalize(row, code, source, confidence, dinh_kem, t_tai, t_ocr):
            nonlocal n_done, outstanding, tong_tai, tong_ocr
            drive_file_id, file_path = row
            update_contract_code_cur(cur, drive_file_id, code, source, confidence, dinh_kem)
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
                        finalize(row, None, "fetch_error", None, None, 0.0, 0.0)
                        continue
                    ocr_fut = ocr_pool.submit(_ocr_task, pdf_bytes)
                    ocr_futs[ocr_fut] = (row, t_tai)
                else:
                    row, t_tai = ocr_futs.pop(fut)
                    try:
                        code, source, confidence, dinh_kem, t_ocr, _err = fut.result()
                    except BrokenProcessPool:
                        print("  !! OCR worker process crash - khởi động lại pool, "
                              "đánh dấu các file đang xử lý dở là lỗi để chạy lại sau.")
                        for f2, (row2, _t) in list(ocr_futs.items()):
                            finalize(row2, None, "worker_crashed", None, None, 0.0, 0.0)
                            ocr_futs.pop(f2, None)
                        ocr_pool.shutdown(wait=False, cancel_futures=True)
                        ocr_pool = make_pool()
                        finalize(row, None, "worker_crashed", None, None, t_tai, 0.0)
                        continue
                    finalize(row, code, source, confidence, dinh_kem, t_tai, t_ocr)

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
    args = parser.parse_args()

    total, done = count_status(nhom=args.nhom)
    pham_vi = f'nhóm "{args.nhom}"' if args.nhom else "toàn bộ"
    print(f"Phạm vi {pham_vi}: {total} hợp đồng, {done} đã có contract_code từ trước (bỏ qua), "
          f"{total - done} còn thiếu.")

    creds = load_credentials()
    rows = fetch_pending(limit=args.limit, nhom=args.nhom)
    print(f"Sẽ xử lý {len(rows)} hợp đồng trong lượt chạy này "
          f"({args.ocr_workers} OCR worker, {args.fetch_threads} fetch thread).")

    t_start = time.perf_counter()
    counts, low_confidence, tong_tai, tong_ocr, n_done = run_pipeline(
        rows, creds, args.ocr_workers, args.fetch_threads
    )
    wall = time.perf_counter() - t_start

    n = n_done or 1
    print(f"\nThời gian trung bình/file: tải {tong_tai / n:.1f}s | xử lý (text-layer/OCR) {tong_ocr / n:.1f}s | "
          f"tổng {(tong_tai + tong_ocr) / n:.1f}s | wall-clock thực tế {wall / n:.1f}s/file "
          f"(tổng {wall:.0f}s cho {n_done} file, chạy song song)")
    print(f"Tổng xử lý: {n_done} | " +
          " | ".join(f"{k}: {v}" for k, v in counts.items()))

    if low_confidence:
        print(f"\n⚠ {len(low_confidence)} hợp đồng cần soi tay (confidence thấp hoặc không đọc được):")
        for file_path, code in low_confidence:
            print(f"   {file_path} -> {code or '(không đọc được)'}")


if __name__ == "__main__":
    main()
