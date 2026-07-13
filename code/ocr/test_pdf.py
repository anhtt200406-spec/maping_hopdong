"""Debug nhanh xem PaddleOCR đọc "chuẩn" không trên 1 PDF có sẵn trên máy
(không qua Drive) - in cả text thô model đọc ra, không chỉ mã cuối cùng, vì
regex có thể "vá" lỗi đọc nhỏ và che mất việc OCR thật ra có đọc sai hay không.

Chỉ in ra terminal - không đụng Postgres/Drive/ghi file. Gọi lại nguyên vẹn
render_header_crop()/ocr_tier1()/extract() y hệt pipeline production
(extract_contract_codes.py), không viết lại logic nào khác.

Cách dùng (từ code/):
    python ocr/test_pdf.py                          # quét hết PDF trong ocr/test_input/
    python ocr/test_pdf.py duong_dan/file1.pdf       # hoặc chỉ định file/thư mục cụ thể
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # PyMuPDF

import header_ocr
from code_extractor import extract

TEST_INPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_input")


def _collect_pdfs(paths):
    if not paths:
        paths = [TEST_INPUT_DIR]
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(
                os.path.join(p, name) for name in os.listdir(p) if name.lower().endswith(".pdf")
            )
        elif p.lower().endswith(".pdf"):
            files.append(p)
    return files


def _print_lines(label, lines):
    print(f"  --- {label} ({len(lines)} dòng) ---")
    if not lines:
        print("    (rỗng)")
    for l in lines:
        print(f"    {l!r}")


def test_one(pdf_path):
    print(f"\n=== {pdf_path} ===")
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text_layer = doc[0].get_text()
    if text_layer.strip():
        text_lines = [l.strip() for l in text_layer.splitlines() if l.strip()]
        _print_lines("text layer PDF (native, không cần OCR)", text_lines)
    else:
        print("  --- text layer PDF: KHÔNG có (PDF scan ảnh, phải OCR) ---")

    for top_ratio, label in ((0.30, "crop30"), (0.55, "crop55")):
        crop = header_ocr.render_header_crop(pdf_bytes, top_ratio=top_ratio)
        lines = header_ocr.ocr_tier1(crop)
        _print_lines(f"OCR {label} (top_ratio={top_ratio})", lines)

    code, source, confidence, dinh_kem, header_text = extract(pdf_bytes)
    print(f"  --- Kết quả extract() thật (pipeline production sẽ ra đúng cái này) ---")
    print(f"    contract_code = {code!r}")
    print(f"    source        = {source!r}")
    print(f"    confidence    = {confidence!r}")
    print(f"    dinh_kem      = {dinh_kem!r}")
    print(f"    header_text   = {header_text!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "paths", nargs="*",
        help=f"File .pdf hoặc thư mục cụ thể. Bỏ trống -> quét {TEST_INPUT_DIR}",
    )
    args = parser.parse_args()

    pdfs = _collect_pdfs(args.paths)
    if not pdfs:
        print(f"Không tìm thấy file .pdf nào trong {TEST_INPUT_DIR if not args.paths else args.paths}. "
              f"Kéo-thả PDF muốn test vào {TEST_INPUT_DIR} rồi chạy lại.")
        return
    print(f"Sẽ test {len(pdfs)} file.")
    for p in pdfs:
        test_one(p)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
