import logging
import time
from pathlib import Path

import requests
import vk_api
from vk_api.upload import VkUpload

log = logging.getLogger("vk_client")

_sessions: dict[str, vk_api.VkApi] = {}


def _session(token: str) -> vk_api.VkApi:
    if token not in _sessions:
        _sessions[token] = vk_api.VkApi(token=token, api_version="5.199")
    return _sessions[token]


def group_info(token: str, group_id: int) -> dict:
    """Проверяет токен и достаёт имя группы (для авто-лейбла при добавлении)."""
    api = _session(token).get_api()
    result = api.groups.getById(group_id=group_id)
    items = result["groups"] if isinstance(result, dict) else result
    return items[0]


def check_wall_photo_upload(token: str, group_id: int) -> None:
    """Проверяет, что токен реально может запросить загрузку фото на стену группы."""
    _session(token).get_api().photos.getWallUploadServer(group_id=group_id)


def _upload_one_photo(upload_url: str, path: str, attempts: int = 3) -> dict:
    """POST одного файла на upload_url. VK иногда молча отдаёт пустой photo при частых
    подряд загрузках (throttling без явной ошибки) — при пустом ответе пробуем ещё раз."""
    data = Path(path).read_bytes()
    for attempt in range(1, attempts + 1):
        resp = requests.post(
            upload_url,
            files={"photo": (Path(path).name, data, "image/jpeg")},
            timeout=60,
        )
        upload_result = resp.json()
        if upload_result.get("photo") and upload_result["photo"] != "[]":
            return upload_result
        log.warning(
            "VK upload server empty photo (attempt %d/%d) status=%s body=%s",
            attempt, attempts, resp.status_code, resp.text[:500],
        )
        if attempt < attempts:
            time.sleep(1.5 * attempt)
    raise RuntimeError(f"VK upload server returned no photo after {attempts} attempts: {upload_result}")


def upload_photos(token: str, group_id: int, paths: list[str]) -> list[str]:
    """Грузит фото по одному через photos.getWallUploadServer.

    vk_api.upload.VkUpload.photo_wall шлёт файл в multipart-поле "file0", а этот
    метод VK ждёт поле именно "photo" — с чужим именем сервер отдаёт пустой ответ
    без ошибки, и saveWallPhoto потом падает с "photo is undefined". Поэтому руками.
    """
    if not paths:
        return []
    api = _session(token).get_api()
    upload_url = api.photos.getWallUploadServer(group_id=group_id)["upload_url"]

    attachments = []
    for path in paths:
        upload_result = _upload_one_photo(upload_url, path)
        saved = api.photos.saveWallPhoto(group_id=group_id, **upload_result)
        attachments.extend(f"photo{p['owner_id']}_{p['id']}" for p in saved)
        time.sleep(0.5)
    return attachments


def upload_video(token: str, group_id: int, path: str, name: str = "") -> str:
    item = VkUpload(_session(token)).video(video_file=path, name=name or "video", group_id=group_id)
    return f"video{item['owner_id']}_{item['video_id']}"


def upload_document(token: str, group_id: int, path: str, name: str) -> str:
    item = VkUpload(_session(token)).document_wall(doc=path, filename=name, group_id=group_id)
    doc = item["doc"] if "doc" in item else item
    return f"doc{doc['owner_id']}_{doc['id']}"


def post_to_wall(token: str, group_id: int, message: str, attachments: list[str] | None = None) -> int:
    api = _session(token).get_api()
    params = {"owner_id": -group_id, "from_group": 1, "message": message or ""}
    if attachments:
        params["attachments"] = ",".join(attachments)
    try:
        result = api.wall.post(**params)
    except Exception:
        log.warning("wall.post failed, params: message=%r attachments=%r", params.get("message"), params.get("attachments"))
        raise
    post_id = result["post_id"]
    log.info("Posted to VK wall: group_id=%s post_id=%s", group_id, post_id)
    return post_id
