"""Benchmark harness cho các phương án tối ưu CPU OCR (xem plan tối ưu hiệu
năng đã duyệt). Trước file này KHÔNG có hạ tầng benchmark nào trong repo (đã
grep toàn bộ + git log -p --all xác nhận) - kết quả test top_ratio ghi trong
Claude.md mục 6 chỉ là văn xuôi, không tái lập được.

Cố tình KHÔNG sửa header_ocr.py để thêm các tham số đang thử nghiệm
(text_det_limit_side_len, dpi, enable_mkldnn, enable_hpi) - script này tự
dựng PaddleOCR + tự monkeypatch header_ocr.render_header_crop trong tiến
trình riêng của nó, để header_ocr.py giữ nguyên hành vi production cho tới
khi có số liệu xác nhận cấu hình nào thắng (xem plan mục "Thứ tự thực hiện").
Vẫn gọi thẳng code_extractor.extract() thật (không viết lại logic regex/
cross-check) để so sánh đúng hành vi pipeline thật, không phải bản rút gọn.

Cách dùng (chạy từ code/, cần .venv active):
    # 1. Tải + cache N PDF thật 1 lần (chỉ cần chạy lại nếu muốn đổi mẫu)
    python ocr/bench_ocr.py cache --n 20

    # 2. Chạy 1 cấu hình - luôn tự spawn subprocess riêng dù gọi trực tiếp,
    #    để 1 config crash cứng (native segfault, không phải Python
    #    exception) không kéo sập các config khác.
    python ocr/bench_ocr.py run --config baseline

    # 3. Chạy hết các cấu hình đã định nghĩa (mỗi cái vẫn 1 subprocess riêng)
    python ocr/bench_ocr.py sweep

    # 4. Chỉ in lại bảng so sánh từ kết quả đã có, không chạy lại gì
    python ocr/bench_ocr.py compare

Lưu ý: đổi paddlepaddle version (mục 1 trong plan) không toggle được bằng
tham số script - phải tự `pip install paddlepaddle==X` trong .venv rồi
chạy lại `run --config mkldnn_on` để so sánh trước/sau downgrade.
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BENCH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bench_cache")
PDF_DIR = os.path.join(BENCH_DIR, "pdfs")
RESULTS_DIR = os.path.join(BENCH_DIR, "results")
MANIFEST_FILE = os.path.join(BENCH_DIR, "manifest.json")

# Mỗi config chỉ đổi ĐÚNG 1 biến so với baseline (trừ khi tên đã nói rõ tổ
# hợp) - để benchmark ra kết luận rạch ròi, không lẫn nhiều biến 1 lúc.
CONFIGS = {
    "baseline": {},
    "mkldnn_on": {"mkldnn": True},
    "det640": {"det_limit_side_len": 640},
    "det480": {"det_limit_side_len": 480},
    "dpi150": {"dpi": 150},
    "dpi300": {"dpi": 300},
    "hpi": {"enable_hpi": True},
}


def _cache_samples(n):
    import drive_auth
    import pdf_fetcher
    from postgres_store import fetch_pending

    os.makedirs(PDF_DIR, exist_ok=True)
    rows = fetch_pending(limit=n)
    if not rows:
        print("Không còn dòng nào 'pending' (contract_code IS NULL) để lấy mẫu.")
        return
    creds = drive_auth.load_credentials()
    service = pdf_fetcher.get_thread_service(creds)
    manifest = []
    for drive_file_id, file_path in rows:
        dest = os.path.join(PDF_DIR, f"{drive_file_id}.pdf")
        if not os.path.exists(dest):
            pdf_bytes = pdf_fetcher.fetch_pdf_bytes(service, drive_file_id)
            with open(dest, "wb") as f:
                f.write(pdf_bytes)
            print(f"  tải: {file_path}")
        else:
            print(f"  đã có sẵn (bỏ qua tải lại): {file_path}")
        manifest.append({"drive_file_id": drive_file_id, "file_path": file_path})
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"Đã cache {len(manifest)} PDF vào {PDF_DIR}")


def _load_manifest():
    if not os.path.exists(MANIFEST_FILE):
        raise SystemExit(
            "Chưa có mẫu nào - chạy `python ocr/bench_ocr.py cache --n 20` trước."
        )
    with open(MANIFEST_FILE, encoding="utf-8") as f:
        return json.load(f)


def _build_patched_ocr_getter(cfg):
    """Trả về hàm thay thế header_ocr._get_tier1(): dựng PaddleOCR với đúng
    tham số baseline (khớp header_ocr._get_tier1 thật) cộng thêm biến số
    đang thử nghiệm trong cfg. Giữ singleton per-process giống bản gốc."""
    from paddleocr import PaddleOCR

    box = {"instance": None}

    def _get():
        if box["instance"] is None:
            cpu_threads = int(os.environ.get("OCR_CPU_THREADS", "1"))
            kwargs = dict(
                lang="vi",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                cpu_threads=cpu_threads,
            )
            if "mkldnn" in cfg:
                kwargs["enable_mkldnn"] = cfg["mkldnn"]
            if cfg.get("det_limit_side_len"):
                kwargs["text_det_limit_side_len"] = cfg["det_limit_side_len"]
            if cfg.get("enable_hpi"):
                kwargs["enable_hpi"] = True
            box["instance"] = PaddleOCR(**kwargs)
        return box["instance"]

    return _get


def _make_crop_renderer(dpi):
    """Bản sao tối thiểu của header_ocr.render_header_crop nhưng dpi đổi
    được - trùng lặp 6 dòng logic thay vì sửa header_ocr.py, vì đây chỉ là
    tham số đang thử nghiệm (xem docstring đầu file)."""
    import fitz
    import numpy as np
    from PIL import Image

    def _render(pdf_bytes, top_ratio=0.30):
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[0]
        rect = page.rect
        clip = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * top_ratio)
        pix = page.get_pixmap(clip=clip, dpi=dpi)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return np.array(img)

    return _render


def _run_config(name):
    cfg = CONFIGS[name]
    # PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT dùng setdefault() ở header_ocr.py nên
    # phải set (hoặc để trống) TRƯỚC khi import header_ocr/paddleocr lần đầu
    # trong tiến trình con này. "run" luôn là tiến trình Python mới (spawn từ
    # sweep(), hoặc gọi tay 1 lệnh riêng) nên set ở đây là an toàn - không
    # ảnh hưởng process khác.
    if cfg.get("mkldnn") is True:
        os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "True"

    import header_ocr
    from code_extractor import extract

    header_ocr._get_tier1 = _build_patched_ocr_getter(cfg)
    if cfg.get("dpi"):
        header_ocr.render_header_crop = _make_crop_renderer(cfg["dpi"])

    manifest = _load_manifest()
    results = {"config": name, "cfg": cfg, "files": []}
    for row in manifest:
        pdf_path = os.path.join(PDF_DIR, f"{row['drive_file_id']}.pdf")
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        t0 = time.perf_counter()
        entry = {"drive_file_id": row["drive_file_id"], "file_path": row["file_path"]}
        try:
            code, source, confidence, _dinh_kem = extract(pdf_bytes)
            entry.update(code=code, source=source, confidence=confidence, error=None)
        except Exception as e:
            entry.update(code=None, source=None, confidence=None, error=repr(e))
        entry["wall_s"] = time.perf_counter() - t0
        results["files"].append(entry)
        print(f"  [{name}] {entry.get('error') or entry['confidence']} "
              f"({entry['wall_s']:.1f}s) {row['file_path']} -> {entry.get('code') or '???'}")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(os.path.join(RESULTS_DIR, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Đã ghi kết quả '{name}' vào {RESULTS_DIR}/{name}.json")


def _sweep(configs):
    for name in configs:
        print(f"\n=== Chạy config '{name}' (subprocess riêng) ===")
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "run", "--config", name]
        )
        if proc.returncode != 0:
            print(f"  !! config '{name}' thoát với mã lỗi {proc.returncode} "
                  f"(có thể crash cứng native - không kéo sập config khác).")
    _compare(configs)


def _compare(configs=None):
    configs = configs or list(CONFIGS)
    loaded = {}
    for name in configs:
        path = os.path.join(RESULTS_DIR, f"{name}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                loaded[name] = json.load(f)
    if not loaded:
        print("Chưa có kết quả nào - chạy `run`/`sweep` trước.")
        return

    baseline_codes = {}
    if "baseline" in loaded:
        baseline_codes = {e["drive_file_id"]: e.get("code") for e in loaded["baseline"]["files"]}

    header = f"{'config':<12} {'n':>3} {'crash':>5} {'high':>5} {'low':>5} {'none':>5} {'avg_s':>7} {'lệch_so_baseline':>16}"
    print("\n" + header)
    print("-" * len(header))
    for name, data in loaded.items():
        files = data["files"]
        n = len(files)
        n_crash = sum(1 for e in files if e.get("error"))
        n_high = sum(1 for e in files if e.get("confidence") == "high")
        n_low = sum(1 for e in files if e.get("confidence") == "low")
        n_none = sum(1 for e in files if not e.get("error") and e.get("code") is None)
        avg_s = sum(e["wall_s"] for e in files) / n if n else 0.0
        n_mismatch = sum(
            1 for e in files
            if baseline_codes and e["drive_file_id"] in baseline_codes
            and e.get("code") != baseline_codes[e["drive_file_id"]]
        ) if name != "baseline" else 0
        print(f"{name:<12} {n:>3} {n_crash:>5} {n_high:>5} {n_low:>5} {n_none:>5} "
              f"{avg_s:>7.2f} {n_mismatch:>16}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cache = sub.add_parser("cache", help="Tải + cache N PDF thật để dùng chung cho mọi config")
    p_cache.add_argument("--n", type=int, default=20)

    p_run = sub.add_parser("run", help="Chạy 1 config, ghi kết quả JSON")
    p_run.add_argument("--config", required=True, choices=list(CONFIGS))

    p_sweep = sub.add_parser("sweep", help="Chạy tất cả config (mỗi cái 1 subprocess) rồi so sánh")
    p_sweep.add_argument("--configs", nargs="*", default=list(CONFIGS), choices=list(CONFIGS))

    sub.add_parser("compare", help="Chỉ in bảng so sánh từ kết quả đã có")

    args = parser.parse_args()
    if args.cmd == "cache":
        _cache_samples(args.n)
    elif args.cmd == "run":
        _run_config(args.config)
    elif args.cmd == "sweep":
        _sweep(args.configs)
    elif args.cmd == "compare":
        _compare()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
