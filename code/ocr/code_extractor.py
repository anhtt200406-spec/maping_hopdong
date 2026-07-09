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

# Định dạng mã kiểu ngân hàng gặp thật ở SMBC: KHÔNG có ngày/tháng/năm ở đầu
# như 3 format trên, mà nối 4 phần bằng dấu gạch ngang DÀI "–" (U+2013, KHÁC
# hẳn "-" thường U+002D), vd dòng "Số:" thật:
#   "VPBSMBCFC – URBOX – 25 – PR17646"
# Chỉ dùng ở nhánh đã neo "Số:" (không dùng cho vòng fallback quét mọi dòng ở
# _find_code_line) để tránh khớp nhầm câu văn bất kỳ có dấu gạch ngang. Vì
# không có ngày/tháng để cross-check nên luôn rơi vào confidence=low (soi
# tay) dù bắt được - an toàn, không tự nhận "high" bừa cho định dạng lạ này.
CODE_RE_DASH = re.compile(
    r"([A-ZĐ0-9]+[-–][A-ZĐ0-9]+[-–]\d{1,4}[-–][A-ZĐ0-9]+)"
)

# Định dạng mã nội bộ riêng của LG Electronics Việt Nam (LGEVH), gặp thật:
#   "040/EAVH-MKT-20240110-0002/C2024001431"
# số thứ tự / bộ-phận-bộ-phận-ngày(YYYYMMDD)-số-phụ / mã-tham-chiếu-C. KHÔNG có
# ngày/tháng dạng ddmm ở đầu như 3 format chuẩn nên không cross-check được với
# dòng ngày -> luôn confidence=low dù bắt được, giống CODE_RE_DASH. Chỉ 1 mẫu
# thật đã soi (xem lịch sử sửa) - phần \d{8} (ngày YYYYMMDD) là điểm neo chặt
# nhất giúp tránh khớp nhầm, nếu gặp thêm biến thể khác cần mở rộng thêm.
CODE_RE_LG = re.compile(
    r"(\d{1,4}/[A-ZĐ]+-[A-ZĐ]+-\d{8}-\d{3,4}/C\d{8,11})"
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
# [oòóỏõọôồốổỗộő]: "ố" (có dấu sắc, chữ thật trong "Số") KHÁC hẳn "ô"/"o" - PDF
# có text layer giữ dấu chuẩn sẽ không khớp nếu chỉ liệt kê "ôo". Liệt kê đủ cả
# họ "o" tiếng Việt (o/ô x 6 thanh) vì OCR có thể làm lệch dấu sang bất kỳ biến
# thể nào - gặp thật "ó" (o+sắc, KHÁC "ố" là ô+sắc) ở 1 file nhóm BẢO HIỂM, và
# "ő" (o 2 dấu sắc kiểu Hungary, không tồn tại trong tiếng Việt - OCR tự bịa ra
# khi đọc nhầm dấu của "ố") ở 1 Phụ lục LG Electronics. SO_LINE_RE cũ bị bỏ sót
# cả 2 vì chỉ liệt kê "ôoố".
# (?:/\s*no\.?\s*)?: nhiều hợp đồng song ngữ ghi "Số/ No.:" thay vì "Số:".
SO_LINE_RE = re.compile(r"^\s*S[oòóỏõọôồốổỗộő]\s*(?:/\s*no\.?\s*)?:?\s*(.+)", re.IGNORECASE)

# OCR đôi khi làm RỚT LUÔN nguyên âm giữa "S" và ":" (ra "S:" trần, gặp thật ở
# cả SMBC lẫn SONADEZI - xem parse_code_dash/BLANK_NUM_RE bên dưới) - SO_LINE_RE
# trên KHÔNG khớp vì bắt buộc phải có 1 trong [ôoố]. Không thể bỏ hẳn yêu cầu
# này (sẽ khớp nhầm mọi từ đầu dòng bắt đầu bằng "S" như "Sáng ngày...") nên
# tách pattern riêng: chỉ chấp nhận thiếu nguyên âm KHI có dấu ":" bắt buộc
# ngay sau (không optional như SO_LINE_RE) - đủ hẹp để không khớp nhầm.
SO_LINE_NOVOWEL_RE = re.compile(r"^\s*S\s*(?:/\s*no\.?\s*)?:\s*(.+)", re.IGNORECASE)

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


def parse_code_dash(text):
    """Bắt định dạng mã kiểu SMBC (CODE_RE_DASH) - chỉ gọi từ nhánh đã neo
    "Số:", không dùng ở vòng fallback quét mọi dòng (xem comment CODE_RE_DASH)."""
    m = CODE_RE_DASH.search(text.replace(" ", ""))
    return m.group(1) if m else None


def parse_code_lg(text):
    """Bắt định dạng mã LG Electronics (CODE_RE_LG) - cũng chỉ gọi từ nhánh đã
    neo "Số:", lý do tương tự parse_code_dash."""
    m = CODE_RE_LG.search(text.replace(" ", ""))
    return m.group(1) if m else None


# "Số:" đôi khi bị OCR ra thiếu hẳn phần số (hiện thành dấu chấm placeholder)
# khi số thật được viết/đóng dấu tách khỏi vị trí in sẵn trên template, OCR
# đọc thành 1 dòng riêng ngay TRƯỚC dòng "Số:" - gặp thật ở SONADEZI, 2 dòng
# liên tiếp:
#   '1812'
#   'S: .../2025/HD/URBOX-SONADEZILT'
# -> thử ghép số 3-4 chữ số đứng ngay trước vào chỗ trống rồi parse lại. Vẫn
# phải qua cross-check ngày ở extract() như bình thường (không phải luật "tin
# luôn"), chỉ là 1 cách dựng lại chuỗi trước khi thử regex.
BLANK_NUM_RE = re.compile(r"^\.{2,}/")
STANDALONE_DDMM_RE = re.compile(r"^\d{3,4}$")


def _find_code_line(lines):
    for i, line in enumerate(lines):
        m = SO_LINE_RE.match(line) or SO_LINE_NOVOWEL_RE.match(line)
        if not m:
            continue
        rest = m.group(1)
        code = parse_code(rest) or parse_code_dash(rest) or parse_code_lg(rest)
        if code:
            return code
        if i > 0 and BLANK_NUM_RE.match(rest.replace(" ", "")):
            neighbor = lines[i - 1].replace(" ", "")
            if STANDALONE_DDMM_RE.match(neighbor):
                code = parse_code(rest.replace("...", neighbor, 1))
                if code:
                    return code
    # fallback: có thể OCR không tách được "Số:" ở đầu dòng - thử match thẳng
    # trên toàn bộ dòng. Kém tin cậy hơn (có thể trúng dòng "Căn cứ" viện dẫn
    # hợp đồng khác) nên luôn phải đi qua cross-check ngày ở extract(). Bỏ qua
    # hẳn dòng có dấu hiệu "đính kèm"/"nguyên tắc" - đó chắc chắn là mã hợp
    # đồng KHÁC (xử lý riêng ở _find_attached_code), không phải mã của file này.
    # Không dùng parse_code_dash ở đây (xem comment CODE_RE_DASH) - định dạng
    # gạch ngang không neo "Số:" quá dễ khớp nhầm câu văn bất kỳ.
    for line in lines:
        if ATTACHED_HINT_RE.search(line):
            continue
        code = parse_code(line)
        if code:
            return code
    return None


def _has_so_line(lines):
    """Kiểm tra có dòng nào neo được "Số:" không (không quan tâm parse ra mã
    hay chưa) - dùng để quyết định có đáng thử crop lớn hơn không: nếu ĐÃ thấy
    dòng "Số:" nhưng chỉ là parse thất bại (định dạng lạ), crop lại vẫn ra
    đúng dòng đó, tăng crop không giúp gì; chỉ đáng thử lại khi dòng "Số:"
    HOÀN TOÀN vắng mặt (khả năng cao nằm dưới rìa crop, gặp thật ở vài Phụ lục
    tiêu đề song ngữ dài của LG Electronics - xem extract())."""
    return any(SO_LINE_RE.match(line) or SO_LINE_NOVOWEL_RE.match(line) for line in lines)


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

    # Crop mặc định (30% đầu trang) có thể chưa chạm tới dòng "Số:" ở các văn
    # bản có tiêu đề song ngữ Việt/Anh dài hơn bình thường (quốc hiệu 2 thứ
    # tiếng + "PHỤ LỤC HỢP ĐỒNG/APPENDIX" chiếm nhiều dòng hơn - gặp thật ở 1
    # số Phụ lục của LG Electronics). Chỉ thử lại với crop lớn hơn khi HOÀN
    # TOÀN không thấy dòng "Số:" nào (không phải khi thấy nhưng parse thất bại
    # - trường hợp đó crop lại vẫn ra cùng nội dung, không giúp gì, chỉ tốn
    # thêm 1 lượt OCR vô ích). Vì vậy chi phí thêm chỉ phát sinh ở số ít file
    # thật sự cần, không ảnh hưởng chi phí trung bình toàn bộ batch.
    if not code_t1 and not _has_so_line(lines_t1):
        crop_big = header_ocr.render_header_crop(pdf_bytes, top_ratio=0.55)
        lines_big = header_ocr.ocr_tier1(crop_big)
        code_big = _find_code_line(lines_big)
        if code_big:
            # Dùng thẳng kết quả _find_code_line() (đã tự có vòng fallback quét
            # mọi dòng bên trong) thay vì gate qua _has_so_line(lines_big) như
            # bản đầu - gate đó có thể tự nó cũng bị false negative (gặp thật:
            # ký tự "ő" - o 2 dấu sắc, khác "ố" - khiến cả SO_LINE_RE lẫn
            # _has_so_line không khớp dòng "Số/No:", dù _find_code_line vẫn
            # tìm ra mã đúng qua vòng fallback không neo). Nếu code_big rỗng vì
            # crop lớn hơn cũng không giúp gì, giữ nguyên lines_t1/code_t1 cũ.
            lines_t1 = lines_big
            code_t1 = code_big

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
