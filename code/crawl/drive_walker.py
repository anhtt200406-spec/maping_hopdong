"""duyệt cây thư mục Drive, resolve shortcut, gom PDF."""

from googleapiclient.errors import HttpError

from config import FOLDER, SHORTCUT, PDF
from drive_auth import execute_with_retry


def list_children(service, folder_id):
    """List toàn bộ con của 1 folder, có phân trang."""
    files, token = [], None
    while True:
        req = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id,name,mimeType,shortcutDetails)",
            pageSize=1000,
            pageToken=token,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            corpora="allDrives",
        )
        resp = execute_with_retry(req.execute)
        files += resp.get("files", [])
        token = resp.get("nextPageToken")
        if not token:
            break
    return files


def resolve(f):
    """Đi theo shortcut -> trả về (id_thật, mime_thật)."""
    if f["mimeType"] == SHORTCUT:
        d = f.get("shortcutDetails", {})
        return d.get("targetId"), d.get("targetMimeType")
    return f["id"], f["mimeType"]


def _shortcut_target_accessible(service, target_id):
    """files.list('X' in parents) KHÔNG báo lỗi nếu X không truy cập được -> chỉ
    trả về rỗng, coi như folder rỗng (mất dữ liệu âm thầm). Phải xác nhận bằng
    files.get trên chính targetId trước khi tin là "folder rỗng thật"."""
    try:
        service.files().get(fileId=target_id, fields="id", supportsAllDrives=True).execute()
        return True
    except HttpError as e:
        return e.resp.status


def walk(service, folder_id, path=(), errors=None):
    """Duyệt đệ quy, yield (path_tuple, file_id) cho mỗi PDF."""
    try:
        children = list_children(service, folder_id)
    except HttpError as e:
        if errors is not None:
            errors.append((path, folder_id, e.resp.status))
        return
    for f in children:
        eid, emime = resolve(f)
        if eid is None:      # shortcut hỏng, không có targetId
            continue
        new_path = path + (f["name"],)
        if f["mimeType"] == SHORTCUT:
            status = _shortcut_target_accessible(service, eid)
            if status is not True:
                if errors is not None:
                    errors.append((new_path, eid, status))
                continue
        if emime == FOLDER:
            yield from walk(service, eid, new_path, errors)
        elif emime == PDF:
            yield new_path, eid
