"""Chuẩn hoá mã hợp đồng để so khớp Tier 1 (exact match).

Đã validate thật trên contracts_db (xem plan): so contracts.contract_code
(OCR bên Drive) với ct_contracts.contract_code (field DB bên urcard-portal)
sau khi normalize_code() ra ~86% khớp đúng cho hợp đồng chính (không phải
phụ lục). Nguyên nhân lệch phổ biến nhất đã thấy thật: chữ "Đ" (URBOX-BAOVIETBANK
bên Drive OCR ra "HD" ASCII, bên urcard field gốc giữ "HĐ" có dấu) và khoảng
trắng thừa quanh dấu "-" (VD "URBOX- PNSHIPS" / "URBOX - CMC TS").
"""

import re
import unicodedata

# Dịch riêng Đ/đ trước: unicodedata NFKD KHÔNG tách được Đ/đ thành D/đ + dấu
# (đây là 1 chữ cái riêng trong bảng chữ cái Việt, không phải D có dấu phụ),
# nên phải translate tay trước khi decompose các dấu thanh/nguyên âm khác.
_DJ_TRANSLATE = str.maketrans("ĐđĐ", "DdD")

_STRIP_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]")


def normalize_code(code):
    """None/rỗng -> None. Ngược lại: upper, Đ/đ->D/d, bỏ dấu thanh/nguyên âm
    (unicodedata NFKD + lọc combining marks), rồi strip mọi ký tự khác
    [A-Z0-9] (bỏ "/", "-", khoảng trắng...). Kết quả rỗng (VD input toàn
    ký tự đặc biệt) -> None, không trả chuỗi rỗng để tránh 2 dòng "không có
    mã" bị coi là khớp nhau qua chuỗi rỗng chung."""
    if not code:
        return None
    s = code.translate(_DJ_TRANSLATE).upper()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _STRIP_NON_ALNUM_RE.sub("", s)
    return s or None


def is_addendum(contract_code, dinh_kem_hop_dong_so=None):
    """Phụ lục (PLHD/PL) - theo chỉ đạo nghiệp vụ, nhóm này KHÔNG cố exact
    match (đã kiểm chứng thật: chỉ 1/74 khớp), luôn đẩy sang Tier 2 text-text.
    dinh_kem_hop_dong_so có giá trị cũng là tín hiệu phụ lục (xem code_extractor.py)."""
    if dinh_kem_hop_dong_so:
        return True
    if not contract_code:
        return False
    upper = contract_code.upper()
    return "PLHD" in upper or "/PL/" in upper
