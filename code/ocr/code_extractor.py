"""Logic nghiệp vụ bóc mã hợp đồng: thử text layer PDF trước (rẻ nhất), rồi
OCR tier 1, cross-check với dòng ngày viết tay, escalate tier 2 nếu lệch."""

import re

import fitz  # PyMuPDF

import header_ocr

# Mục đích: bắt mã hợp đồng chuẩn Việt Nam, "loại" là phần optional.
# Dữ liệu - 3 biến thể đã gặp thật:
#   "2503/2025/HĐDV/AGRIBANK-TOQUA"   số/năm/loại/bên-bên
#   "2003/2026/TOQUA-ANTSOMI"         số/năm/bên-bên (không loại, hợp đồng NDA)
#   "2703/2025HĐNT/URBOX-NVC"         năm+loại dính liền, không "/"
# Lưu ý: bắt buộc phần "bên-bên" có dấu "-" để không khớp nhầm 1 chuỗi
# chữ/số bất kỳ đứng sau 2 mốc số/năm.
CODE_RE = re.compile(
    r"(\d{1,4}/\d{4}(?:/?[A-ZĐ]+)?/[A-ZĐ0-9]+(?:-[A-ZĐ0-9]+)+)"
)

# Mục đích: mã kiểu SMBC - nối 4 phần bằng gạch ngang DÀI "–" (U+2013, khác
# "-" thường), không có ngày/tháng nên không cross-check được.
# Dữ liệu: "VPBSMBCFC – URBOX – 25 – PR17646".
# Lưu ý: chỉ dùng ở nhánh đã neo "Số:", không dùng ở fallback quét mọi dòng -
# định dạng gạch ngang trần rất dễ khớp nhầm câu văn thường. Luôn confidence=low.
CODE_RE_DASH = re.compile(
    r"([A-ZĐ0-9]+[-–][A-ZĐ0-9]+[-–]\d{1,4}[-–][A-ZĐ0-9]+)"
)

# Mục đích: mã nội bộ riêng của LG Electronics VN (LGEVH).
# Dữ liệu: "040/EAVH-MKT-20240110-0002/C2024001431" (số/bộ phận-ngày YYYYMMDD-
# số phụ/mã tham chiếu). Không cross-check được ngày -> luôn confidence=low.
# Lưu ý: mới soi 1 mẫu thật, \d{8} là điểm neo chính - gặp biến thể khác thì mở rộng thêm.
CODE_RE_LG = re.compile(
    r"(\d{1,4}/[A-ZĐ]+-[A-ZĐ]+-\d{8}-\d{3,4}/C\d{8,11})"
)

# Mục đích: biến thể thiếu hẳn năm (văn bản gốc vốn vậy, không phải OCR sót),
# gặp thật ở SHINHAN BẮC NINH.
# Dữ liệu: "0303/HĐ/URBOX-SHINHANBACNINH" (ký 03/03).
# Lưu ý: "loại" ở đây bắt buộc (khác CODE_RE coi optional) vì thiếu 1 nhóm số
# rồi, không neo thêm thì độ đặc thù quá thấp. Vẫn cross-check ngày bình thường.
CODE_RE_NOYEAR = re.compile(
    r"(\d{1,4}/[A-ZĐ]+/[A-ZĐ0-9]+(?:-[A-ZĐ0-9]+)+)"
)

# Mục đích: ngày+tháng+năm gộp liền 8 số, không "/" - gặp thật ở G HOMES (text
# layer, không OCR).
# Dữ liệu: "08012025/GHOMES-URBOX" (08/01/2025 viết liền).
# Lưu ý: _code_matches_date() phải tự cắt 4 số đầu của prefix 8 số này để so ddmm.
CODE_RE_COMPACT8 = re.compile(
    r"(\d{8}/[A-ZĐ0-9]+(?:-[A-ZĐ0-9]+)+)"
)

# vd "ngày 25 tháng 03 năm 2025"
DATE_LINE_RE = re.compile(
    r"ng[aà]y\s*(\d{1,2}).*?th[aá]ng\s*(\d{1,2}).*?n[aă]m\s*(\d{4})",
    re.IGNORECASE,
)

# Mục đích: nhận diện dòng "Số:" mở đầu mã hợp đồng, phải đứng đầu dòng
# (tránh khớp nhầm câu "Căn cứ hợp đồng số: ..." viện dẫn hợp đồng khác).
# Dữ liệu: "Số:", "(Số: ...)", "Số/No.:", và biến thể OCR đọc lệch dấu
# (ố/ó/ő - gặp thật ở BẢO HIỂM, LG).
# Lưu ý: \b sau lớp nguyên âm là bắt buộc - thiếu nó regex ăn luôn chữ
# "SOCIALIST" trong quốc hiệu song ngữ, mất mã thật (đã dính bug này rồi, đừng bỏ).
SO_LINE_RE = re.compile(r"^\s*\(?\s*S[oòóỏõọôồốổỗộő]\b\s*(?:/\s*no\.?\s*)?:?\s*(.+)", re.IGNORECASE)

# Mục đích: bắt trường hợp OCR rớt mất nguyên âm giữa "S" và ":" (ra "S:"
# trần), gặp thật ở SMBC/SONADEZI - SO_LINE_RE không khớp vì thiếu nguyên âm.
# Lưu ý: bắt buộc phải có ":" ngay sau "S", nếu không sẽ khớp nhầm mọi từ bắt
# đầu bằng "S" như "Sáng ngày...".
SO_LINE_NOVOWEL_RE = re.compile(r"^\s*\(?\s*S\s*(?:/\s*no\.?\s*)?:\s*(.+)", re.IGNORECASE)

# Mục đích: OCR rớt cả nguyên âm lẫn ":", chỉ còn "S" + khoảng trắng + số -
# gặp thật ở SHINHAN BẮC NINH: "S 0303/HD/URBOX-SHINHANBACNINH".
# Lưu ý: bắt buộc số 3-4 chữ số + "/" ngay sau, để không khớp nhầm từ tiếng
# Việt thường bắt đầu bằng "S".
SO_LINE_NOVOWEL_NOCOLON_RE = re.compile(r"^\s*S\s+(\d{3,4}/.+)", re.IGNORECASE)

# Mục đích: Phụ lục/văn bản đính kèm thường không có "Số:" riêng, chỉ nhắc
# tới hợp đồng nguyên tắc khác - lấy mã của hợp đồng đó làm contract_code.
# Dữ liệu (không neo đầu dòng vì cụm này thường nằm giữa câu/trong ngoặc):
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


def parse_code_noyear(text):
    """Bắt định dạng thiếu năm (CODE_RE_NOYEAR) - chỉ gọi từ nhánh đã neo
    "Số:", lý do tương tự parse_code_dash."""
    m = CODE_RE_NOYEAR.search(text.replace(" ", ""))
    return m.group(1) if m else None


def parse_code_compact8(text):
    """Bắt định dạng ngày gộp liền 8 số (CODE_RE_COMPACT8) - chỉ gọi từ nhánh
    đã neo "Số:", lý do tương tự parse_code_dash."""
    m = CODE_RE_COMPACT8.search(text.replace(" ", ""))
    return m.group(1) if m else None


# Mục đích: số hợp đồng thật bị viết tách khỏi template in sẵn, OCR đọc
# thành 1 dòng riêng NGAY TRƯỚC dòng "Số:" - gặp thật ở SONADEZI:
#   '1812'
#   'S: .../2025/HD/URBOX-SONADEZILT'
# Lưu ý: ghép số đứng trước vào chỗ trống rồi parse lại, vẫn phải qua
# cross-check ngày như bình thường.
BLANK_NUM_RE = re.compile(r"^\.{2,}/")
STANDALONE_DDMM_RE = re.compile(r"^\d{3,4}$")


def _find_code_line(lines):
    for i, line in enumerate(lines):
        m = (SO_LINE_RE.match(line) or SO_LINE_NOVOWEL_RE.match(line)
             or SO_LINE_NOVOWEL_NOCOLON_RE.match(line))
        if not m:
            continue
        rest = m.group(1)
        code = (parse_code(rest) or parse_code_dash(rest) or parse_code_lg(rest)
                or parse_code_noyear(rest) or parse_code_compact8(rest))
        if code:
            return code
        if i > 0 and BLANK_NUM_RE.match(rest.replace(" ", "")):
            neighbor = lines[i - 1].replace(" ", "")
            if STANDALONE_DDMM_RE.match(neighbor):
                code = parse_code(rest.replace("...", neighbor, 1))
                if code:
                    return code
    # Fallback: không tách được dòng "Số:" thì quét thẳng mọi dòng - kém tin
    # cậy hơn nên luôn phải qua cross-check ngày. Bỏ qua dòng "đính kèm"/
    # "nguyên tắc" (đó là mã hợp đồng khác, xử lý ở _find_attached_code).
    # Không dùng parse_code_dash ở đây - gạch ngang trần không neo "Số:" quá
    # dễ khớp nhầm câu văn bất kỳ.
    for line in lines:
        if ATTACHED_HINT_RE.search(line):
            continue
        code = parse_code(line)
        if code:
            return code
    return None


def _has_so_line(lines):
    """Có thấy dòng "Số:" không (không quan tâm parse ra mã hay chưa) - dùng
    để quyết định có đáng crop lại to hơn không. Thấy dòng nhưng parse fail
    thì crop lại cũng vô ích; chỉ đáng thử khi dòng "Số:" vắng mặt hẳn (khả
    năng nằm ngoài rìa crop, gặp ở vài Phụ lục tiêu đề song ngữ dài của LG)."""
    return any(SO_LINE_RE.match(line) or SO_LINE_NOVOWEL_RE.match(line)
               or SO_LINE_NOVOWEL_NOCOLON_RE.match(line) for line in lines)


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
    # CODE_RE_COMPACT8 gộp liền 8 số (ddmmyyyy) - chỉ so 4 số đầu (ddmm),
    # so nguyên 8 số sẽ luôn lệch dù đúng.
    if len(prefix) == 8 and prefix.isdigit():
        prefix = prefix[:4]
    return prefix.zfill(4) == ddmm or prefix == ddmm


def _text_layer_lines(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = doc[0].get_text()
    if not text.strip():
        return None  # PDF scan ảnh, không có text layer
    return [l.strip() for l in text.splitlines() if l.strip()]


def extract(pdf_bytes):
    """Bóc mã hợp đồng từ 1 PDF: thử text layer trước, không có thì OCR.
    Trả về (contract_code, source, confidence, dinh_kem_hop_dong_so, header_text).
    contract_code=None = không đọc được, để soi tay, không bao giờ bịa số.
    dinh_kem_hop_dong_so chỉ có giá trị khi mã là MƯỢN từ hợp đồng nguyên tắc
    (văn bản này không có Số: riêng, vd Phụ lục). header_text là text đã đọc
    để tìm mã (cả trang 1 nếu PDF gốc, vùng crop nếu phải OCR) - phục vụ
    mapping/tra cứu ở Pass 3, không chỉ để debug.

    Lưu ý: MỌI nguồn (kể cả text layer) đều phải cross-check ngày trước khi
    gắn confidence=high - từng có bug chọn nhầm dòng ở cả 2 nguồn, không có
    ngoại lệ "tin luôn"."""
    lines_text = _text_layer_lines(pdf_bytes)
    code_text = _find_code_line(lines_text) if lines_text else None
    if code_text:
        ddmm_text = _find_written_date_ddmm(lines_text)
        if _code_matches_date(code_text, ddmm_text):
            return code_text, "pdf_text", "high", None, "\n".join(lines_text)

    crop = header_ocr.render_header_crop(pdf_bytes)
    lines_t1 = header_ocr.ocr_tier1(crop)
    code_t1 = _find_code_line(lines_t1)

    # Escalate crop 30% -> 55%: chỉ khi HOÀN TOÀN không thấy dòng "Số:" nào
    # (tiêu đề song ngữ dài che khuất, gặp ở vài Phụ lục LG). Nếu thấy dòng
    # "Số:" nhưng parse fail thì crop lại vẫn ra y hệt, không thử lại cho đỡ
    # tốn 1 lượt OCR vô ích.
    if not code_t1 and not _has_so_line(lines_t1):
        crop_big = header_ocr.render_header_crop(pdf_bytes, top_ratio=0.55)
        lines_big = header_ocr.ocr_tier1(crop_big)
        code_big = _find_code_line(lines_big)
        if code_big:
            # Tin thẳng code_big (không gate qua _has_so_line lần 2) - gate
            # đó từng false-negative với ký tự "ő" (gặp thật), trong khi
            # fallback quét mọi dòng của _find_code_line vẫn ra đúng mã.
            lines_t1 = lines_big
            code_t1 = code_big

    # Text layer sạch hơn OCR (không lỗi đọc nhầm) nên ưu tiên khi có sẵn;
    # header_text dùng chung cho mọi kết quả từ đây trở xuống, kể cả confidence=low.
    header_text = "\n".join(lines_text) if lines_text else "\n".join(lines_t1)
    ddmm_t1 = _find_written_date_ddmm(lines_t1)

    if code_t1 and _code_matches_date(code_t1, ddmm_t1):
        return code_t1, "ocr_tier1", "high", None, header_text

    # Text layer và OCR không tự cross-check được (thiếu dòng ngày / lệch),
    # nhưng nếu CẢ HAI nguồn độc lập đều ra cùng 1 mã thì vẫn đủ tin cậy.
    if code_text and code_text == code_t1:
        return code_t1, "pdf_text+ocr_tier1", "high", None, header_text

    # Không tìm được "Số:" riêng ở cả 2 nguồn -> có thể đây là Phụ lục/văn bản
    # đính kèm, không có mã độc lập -> thử lấy mã hợp đồng nguyên tắc mà nó
    # đính kèm/căn cứ vào, dùng luôn mã đó làm contract_code.
    if not code_text and not code_t1:
        attached = _find_attached_code(lines_text) if lines_text else None
        if not attached:
            attached = _find_attached_code(lines_t1)
        if attached:
            return attached, "attached_parent", "high", attached, header_text

    # Đáng lẽ escalate sang PaddleOCR-VL (tier 2), nhưng tier 2 CHƯA cài
    # (thiếu extras `paddlex[ocr]`, xem Claude.md mục 12/7). Không bịa số:
    # trả về mã đọc được (nếu có) với confidence=low để vào hàng soi tay.
    final_code = code_t1 or code_text
    source = "ocr_tier1" if code_t1 else ("pdf_text" if code_text else None)
    return final_code, source, "low", None, header_text
