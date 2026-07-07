"""chuẩn hóa tên nhóm/brand, ngày, dựng rows để upsert."""

import re
from datetime import date

from config import ROOT_FOLDER_ID, FOLDER
from drive_walker import walk, list_children, resolve


def clean_nhom(s):
    return re.sub(r"^\s*Nhóm\s*:\s*", "", s).strip()


def clean_brand(s):
    return re.sub(r"^\s*\[[^\]]+\]\s*", "", s).strip()  # bỏ [PRT], [CLIENT]...


def parse_date(name):
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", name)
    if not m:
        return None
    d, mo, y = map(int, m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _norm_for_match(s):
    """Chuẩn hóa để so khớp tên nhóm: gộp khoảng trắng quanh dấu '-' (tên nhóm
    thực tế không đồng nhất, ví dụ "HÀNG- TÀI" vs "HÀNG - TÀI"), gộp khoảng
    trắng thừa, hạ hoa/thường."""
    s = re.sub(r"\s*-\s*", "-", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s

"""Tìm folder con của ROOT_FOLDER_ID có tên chứa nhom_query. Trả về
(id_thật, tên_raw)."""
def find_nhom_folder(service, nhom_query):
    query = _norm_for_match(nhom_query)
    matches = []
    for f in list_children(service, ROOT_FOLDER_ID):
        eid, emime = resolve(f)
        if eid is None or emime != FOLDER:
            continue
        raw = f["name"]
        if query in _norm_for_match(clean_nhom(raw)) or query in _norm_for_match(raw):
            matches.append((eid, raw))
    if not matches:
        raise ValueError(f"Không tìm thấy nhóm nào khớp '{nhom_query}' dưới root.")
    if len(matches) > 1:
        names = ", ".join(f'"{raw}"' for _, raw in matches)
        raise ValueError(f"'{nhom_query}' khớp {len(matches)} nhóm: {names}. Gõ rõ hơn để phân biệt.")
    return matches[0]


def build_rows(service, nhom=None):
    rows, flagged, errors = [], [], []
    if nhom:
        start_id, raw_name = find_nhom_folder(service, nhom)
        start_path = (raw_name,)
    else:
        start_id, start_path = ROOT_FOLDER_ID, ()
    for path, fid in walk(service, start_id, path=start_path, errors=errors):
        if len(path) < 3:
            flagged.append((path, fid))   # PDF nằm cao bất thường -> soi tay
            continue
        nhom_raw, brand_raw, ten = path[0], path[1], path[-1]
        rows.append((
            clean_nhom(nhom_raw),
            clean_brand(brand_raw),
            brand_raw,
            ten,
            parse_date(ten),
            fid,
            "/".join(path),
        ))
    return rows, flagged, errors
