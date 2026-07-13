"""Crop vùng header trang 1 + chạy OCR (tier 1 = PP-OCRv6, tier 2 = PaddleOCR-VL, chưa bật).

Lưu ý: paddlepaddle đang ghim 3.2.1 (xem requirements.txt) - bản 3.3.0+ từng
crash khi bật mkldnn (PaddleOCR#18162). Nếu sau này nâng version, chạy lại
`python ocr/bench_ocr.py run --config mkldnn_on` để chắc chắn trước khi tin."""

import os

import fitz  # PyMuPDF
import numpy as np
from PIL import Image

_tier1 = None
_tier2 = None


def _get_tier1():
    global _tier1
    if _tier1 is None:
        from paddleocr import PaddleOCR
        # Ghim cpu_threads=1: mặc định paddleocr là 10/instance, chạy song
        # song nhiều process mà không ghim sẽ tranh CPU lẫn nhau. Đọc qua env
        # var vì orchestrator set trước khi fork worker (extract_contract_codes.py).
        cpu_threads = int(os.environ.get("OCR_CPU_THREADS", "1"))
        _tier1 = PaddleOCR(
            lang="vi",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            cpu_threads=cpu_threads,
        )
    return _tier1


def _get_tier2():
    global _tier2
    if _tier2 is None:
        from paddleocr import PaddleOCRVL
        _tier2 = PaddleOCRVL(use_layout_detection=False)
    return _tier2


def render_header_crop(pdf_bytes, top_ratio=0.30):
    """Cắt vùng top_ratio đầu trang 1 (chứa dòng 'Số:' và dòng 'ngày...tháng...
    năm'), trả về ảnh numpy RGB để OCR."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    rect = page.rect
    clip = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * top_ratio)
    pix = page.get_pixmap(clip=clip, dpi=200)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return np.array(img)


def _extract_lines(result):
    """OCRResult (tier1) và PaddleOCRVLResult (tier2, chế độ spotting, đã tắt
    layout_detection) đều cho text đọc được, chỉ khác chỗ lưu (rec_texts ở gốc
    hay trong spotting_res) - truy cập thống nhất qua hàm này."""
    res = result["spotting_res"] if "spotting_res" in result else result
    return list(res["rec_texts"])


def ocr_tier1(image):
    lines = []
    for r in _get_tier1().predict(image):
        lines.extend(_extract_lines(r))
    return lines


def ocr_tier2(image):
    lines = []
    for r in _get_tier2().predict(image):
        lines.extend(_extract_lines(r))
    return lines
