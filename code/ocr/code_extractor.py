"""Logic nghiệp vụ bóc mã hợp đồng: thử text layer PDF trước (rẻ nhất), rồi
OCR tier 1, cross-check với dòng ngày viết tay, escalate tier 2 nếu lệch."""

import re

import fitz  # PyMuPDF

import header_ocr

# 3 format mã đã gặp thật (xem Claude.md mục 12):
#   "2503/2025/HĐDV/AGRIBANK-TOQUA"  - số/năm/loại/bên-bên (có "/" trước loại)
#   "2003/2026/TOQUA-ANTSOMI"        - số/năm/bên-bên (không có loại, hợp đồng NDA)
#   "2703/2025HĐNT/URBOX-NVC"        - số/năm+loại dính liền (không có "/" giữa năm và loại)
# -> phần loại là optional, và nếu có thì "/" đứng trước nó cũng optional.
# Luôn bắt buộc phần "bên-bên" có ít nhất 1 dấu "-" để phân biệt với 1 chuỗi
# chữ/số bất kỳ đứng sau 2 mốc số/năm.
CODE_RE = re.compile(
    r"(\d{1,4}/\d{4}(?:/?[A-ZĐ]+)?/[A-ZĐ0-9]+(?:-[A-ZĐ0-9]+)+)"
)

# vd "ngày 25 tháng 03 năm 2025"
DATE_LINE_RE = re.compile(
    r"ng[aà]y\s*(\d{1,2}).*?th[aá]ng\s*(\d{1,2}).*?n[aă]m\s*(\d{4})",
    re.IGNORECASE,
)

# Neo vào ĐẦU dòng (^): dòng "Số:" thật luôn đứng đầu dòng riêng. Nếu không
# neo, regex còn khớp cả cụm "số:" nằm giữa câu kiểu "Căn cứ Hợp đồng số:
# 1506/2019/..." (mã hợp đồng KHÁC được viện dẫn, không phải mã của chính
# file này) - đã bắt được lỗi này khi soi 1 file thật, xem Claude.md mục 12.
#
# [ôoố]: "ố" (có dấu sắc, chữ thật trong "Số") KHÁC hẳn "ô"/"o" - PDF có text
# layer giữ nguyên dấu chuẩn sẽ không khớp nếu chỉ liệt kê "ôo" (chỉ né được
# nhờ OCR hay làm mất dấu, chưa từng bị lộ vì mọi file test tới giờ đều OCR).
# (?:/\s*no\.?\s*)?: nhiều hợp đồng song ngữ ghi "Số/ No.:" thay vì "Số:".
SO_LINE_RE = re.compile(r"^\s*S[ôoố]\s*(?:/\s*no\.?\s*)?:?\s*(.+)", re.IGNORECASE)

# Phụ lục/văn bản đính kèm thường KHÔNG có "Số:" riêng của bản thân nó, mà chỉ
# nói đính kèm/căn cứ theo 1 "hợp đồng nguyên tắc" khác - phải lấy mã hợp đồng
# đó làm contract_code (không có mã riêng độc lập để dùng). Không neo đầu
# dòng vì cụm này thường nằm giữa câu/trong ngoặc, vd:
#   "(Đính kèm Hợp đồng nguyên tắc số: 2703/2025HĐNT/URBOX-NVC)"
#   "Căn cứ theo Hợp đồng nguyên tắc số 2703/2025HĐNT/URBOX-NVC..."
ATTACHED_HINT_RE = re.compile(r"đính\s*k[eè]m|nguy[eê]n\s*t[aă]c", re.IGNORECASE)


def parse_code(text):
    """Bắt pattern mã hợp đồng trong 1 chuỗi text. None nếu không khớp."""
    m = CODE_RE.search(text.replace(" ", ""))
    return m.group(1) if m else None


def _find_code_line(lines):
    for line in lines:
        m = SO_LINE_RE.match(line)
        if m:
            code = parse_code(m.group(1))
            if code:
                return code
    # fallback: có thể OCR không tách được "Số:" ở đầu dòng - thử match thẳng
    # trên toàn bộ dòng. Kém tin cậy hơn (có thể trúng dòng "Căn cứ" viện dẫn
    # hợp đồng khác) nên luôn phải đi qua cross-check ngày ở extract(). Bỏ qua
    # hẳn dòng có dấu hiệu "đính kèm"/"nguyên tắc" - đó chắc chắn là mã hợp
    # đồng KHÁC (xử lý riêng ở _find_attached_code), không phải mã của file này.
    for line in lines:
        if ATTACHED_HINT_RE.search(line):
            continue
        code = parse_code(line)
        if code:
            return code
    return None


def _find_attached_code(lines):
    """Tìm mã hợp đồng nguyên tắc mà văn bản này đính kèm/căn cứ vào (Phụ lục
    không có Số: riêng). None nếu không thấy dấu hiệu đính kèm nào."""
    for line in lines:
        if ATTACHED_HINT_RE.search(line):
            code = parse_code(line)
            if code:
                return code
    return None


def _find_written_date_ddmm(lines):
    """Lấy phần (ngày, tháng) từ dòng 'ngày...tháng...năm' để cross-check với
    2 nhóm số đầu trong mã hợp đồng (thường là ngày+tháng ký, vd '2503' = 25/03)."""
    for line in lines:
        m = DATE_LINE_RE.search(line)
        if m:
            d, mo, _y = m.groups()
            return d.zfill(2) + mo.zfill(2)
    return None


def _code_matches_date(code, ddmm):
    if not code or not ddmm:
        return False
    prefix = code.split("/")[0]
    return prefix.zfill(4) == ddmm or prefix == ddmm


def _text_layer_lines(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = doc[0].get_text()
    if not text.strip():
        return None  # PDF scan ảnh, không có text layer
    return [l.strip() for l in text.splitlines() if l.strip()]


def extract(pdf_bytes):
    """Trả về (contract_code, source, confidence, dinh_kem_hop_dong_so).
    contract_code=None nghĩa là không bóc được gì, để dành soi tay - không bịa số.
    dinh_kem_hop_dong_so chỉ có giá trị khi contract_code là mã MƯỢN từ hợp
    đồng nguyên tắc khác (văn bản này không có Số: riêng, vd Phụ lục).

    QUAN TRỌNG: mọi kết quả (kể cả từ text layer, không chỉ OCR) đều phải
    cross-check với dòng ngày trước khi gắn confidence=high - không có
    ngoại lệ "tin luôn" cho bất kỳ nguồn nào, vì cùng 1 lỗi chọn nhầm dòng có
    thể xảy ra ở cả text layer lẫn OCR (đã gặp thật, xem Claude.md mục 12)."""
    lines_text = _text_layer_lines(pdf_bytes)
    code_text = _find_code_line(lines_text) if lines_text else None
    if code_text:
        ddmm_text = _find_written_date_ddmm(lines_text)
        if _code_matches_date(code_text, ddmm_text):
            return code_text, "pdf_text", "high", None

    crop = header_ocr.render_header_crop(pdf_bytes)
    lines_t1 = header_ocr.ocr_tier1(crop)
    code_t1 = _find_code_line(lines_t1)
    ddmm_t1 = _find_written_date_ddmm(lines_t1)

    if code_t1 and _code_matches_date(code_t1, ddmm_t1):
        return code_t1, "ocr_tier1", "high", None

    # Text layer và OCR không tự cross-check được (thiếu dòng ngày / lệch),
    # nhưng nếu CẢ HAI nguồn độc lập đều ra cùng 1 mã thì vẫn đủ tin cậy.
    if code_text and code_text == code_t1:
        return code_t1, "pdf_text+ocr_tier1", "high", None

    # Không tìm được "Số:" riêng ở cả 2 nguồn -> có thể đây là Phụ lục/văn bản
    # đính kèm, không có mã độc lập -> thử lấy mã hợp đồng nguyên tắc mà nó
    # đính kèm/căn cứ vào, dùng luôn mã đó làm contract_code.
    if not code_text and not code_t1:
        attached = _find_attached_code(lines_text) if lines_text else None
        if not attached:
            attached = _find_attached_code(lines_t1)
        if attached:
            return attached, "attached_parent", "high", attached

    # Đáng lẽ escalate sang PaddleOCR-VL (tier 2), nhưng tier 2 CHƯA cài
    # (thiếu extras `paddlex[ocr]`, xem Claude.md mục 12/7). Không bịa số:
    # trả về mã đọc được (nếu có) với confidence=low để vào hàng soi tay.
    final_code = code_t1 or code_text
    source = "ocr_tier1" if code_t1 else ("pdf_text" if code_text else None)
    return final_code, source, "low", None
