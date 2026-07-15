"""Đọc contract_code + header_text từ file KHÔNG phải PDF (docx/ảnh) tải về
từ urcard-portal (3858/4311 dòng ct_contracts là .pdf, dùng thẳng
code_extractor.extract() không cần file này - xem fetch_ct_contracts.py).

KHÔNG sửa code_extractor.py - import thẳng các hàm helper "riêng tư" (dấu
`_`) đã có sẵn để tái dùng đúng logic tìm dòng "Số:" / cross-check ngày,
thay vì viết lại regex. Python cho phép import tên có `_`, đây chỉ là quy
ước, không phải giới hạn kỹ thuật.
"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # code/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ocr"))  # code/ocr/

import numpy as np
from docx import Document
from PIL import Image

import header_ocr
from code_extractor import (
    _code_matches_date,
    _find_attached_code,
    _find_code_line,
    _find_written_date_ddmm,
    _has_so_line,
)


def _extract_from_lines(lines, source_prefix):
    """Ráp lại đúng thứ tự logic của code_extractor.extract() cho 1 list
    dòng text đã có sẵn (không cần OCR nữa, vì docx/OCR-ảnh đã cho ra lines
    rồi): tìm dòng "Số:" -> cross-check ngày -> confidence=high; không cross-
    check được thì thử "đính kèm" (phụ lục căn cứ hợp đồng gốc); cuối cùng
    trả về mã đọc được (nếu có) với confidence=low, không bao giờ bịa số."""
    if not lines:
        return None, None, "low", None, ""

    header_text = "\n".join(lines)
    code = _find_code_line(lines)
    if code:
        ddmm = _find_written_date_ddmm(lines)
        if _code_matches_date(code, ddmm):
            return code, source_prefix, "high", None, header_text

    if not code:
        attached = _find_attached_code(lines)
        if attached:
            return attached, "attached_parent", "high", attached, header_text

    return code, (source_prefix if code else None), "low", None, header_text


def extract_docx(docx_bytes):
    """docx luôn có sẵn text layer (giống Word) - không bao giờ cần OCR, độ
    tin cậy tương đương nhánh pdf_text của code_extractor.extract().
    Trả về (contract_code, source, confidence, dinh_kem_hop_dong_so, header_text)."""
    doc = Document(io.BytesIO(docx_bytes))
    lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return _extract_from_lines(lines, "docx_text")


def _crop_top(image, top_ratio):
    h = image.shape[0]
    return image[: int(h * top_ratio), :]


def extract_image(image_bytes):
    """Ảnh scan trần (.jpg/.png, chỉ vài file trong ct_contracts) - không có
    PDF bọc ngoài nên bỏ qua fitz, decode thẳng rồi OCR như nhánh OCR của
    code_extractor.extract() (kể cả bước escalate crop 30%->55% nếu không
    thấy dòng "Số:" trong crop nhỏ)."""
    image = np.array(Image.open(io.BytesIO(image_bytes)).convert("RGB"))

    crop = _crop_top(image, 0.30)
    lines = header_ocr.ocr_tier1(crop)

    if not _find_code_line(lines) and not _has_so_line(lines):
        crop_big = _crop_top(image, 0.55)
        lines_big = header_ocr.ocr_tier1(crop_big)
        if lines_big:
            lines = lines_big

    return _extract_from_lines(lines, "ocr_tier1")
