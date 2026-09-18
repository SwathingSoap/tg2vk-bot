import logging
import time
from pathlib import Path

import requests
import vk_api
from vk_api.exceptions import ApiError
from vk_api.upload import VkUpload

log = logging.getLogger("vk_client")

FLOOD_CONTROL = 9
# VK отдаёт [9] Flood control на короткое окно; ждём и пробуем ещё раз. Очередь постов
# однопоточная, так что сон здесь просто притормаживает следующий пост, ничего не теряя.
FLOOD_RETRY_DELAYS = (10, 30, 90)
# upload_url от photos.getWallUploadServer живёт часами, а сам метод VK лимитирует жёстко —
# именно его дёрганье на каждый пост и приводило к [9] Flood control.
UPLOAD_URL_TTL = 20 * 60

_sessions: dict[str, vk_api.VkApi] = {}
_upload_urls: dict[tuple[str, int], tuple[str, float]] = {}


def _session(token: str) -> vk_api.VkApi:
    if token not in _sessions:
        _sessions[token] = vk_api.VkApi(token=token, api_version="5.199")
    return _sessions[token]


def _call(method, name: str, **kwargs):
    """Вызов метода VK с повтором на [9] Flood control и логом причины при провале."""
    for delay in (*FLOOD_RETRY_DELAYS, None):
        try:
            return method(**kwargs)
        except ApiError as exc:
            if exc.code != FLOOD_CONTROL or delay is None:
                log.exception("VK %s failed: %s", name, kwargs)
                raise
            log.warning("VK %s: flood control, повтор через %ss (%s)", name, delay, kwargs)
            time.sleep(delay)
        except Exception:
            log.exception("VK %s failed: %s", name, kwargs)
            raise


def group_info(token: str, group_id: int) -> dict:
    """Проверяет токен и достаёт имя группы (для авто-лейбла при добавлении)."""
    api = _session(token).get_api()
    result = _call(api.groups.getById, "groups.getById", group_id=group_id)
    items = result["groups"] if isinstance(result, dict) else result
    return items[0]


def check_wall_photo_upload(token: str, group_id: int) -> None:
    """Проверяет, что токен реально может запросить загрузку фото на стену группы."""
    _wall_upload_url(token, group_id)


def _wall_upload_url(token: str, group_id: int, refresh: bool = False) -> str:
    key = (token, group_id)
    cached = _upload_urls.get(key)
    if not refresh and cached and time.time() - cached[1] < UPLOAD_URL_TTL:
        return cached[0]

    api = _session(token).get_api()
    url = _call(api.photos.getWallUploadServer, "photos.getWallUploadServer", group_id=group_id)["upload_url"]
    _upload_urls[key] = (url, time.time())
    return url


def _post_photo(upload_url: str, path: str) -> dict | None:
    """POST одного файла на upload_url. Возвращает None, если сервер отдал пустой photo."""
    data = Path(path).read_bytes()
    resp = requests.post(
        upload_url,
        files={"photo": (Path(path).name, data, "image/jpeg")},
        timeout=60,
    )
    upload_result = resp.json()
    if upload_result.get("photo") and upload_result["photo"] != "[]":
        return upload_result
    log.warning(
        "VK upload server empty photo: path=%s status=%s body=%s",
        path, resp.status_code, resp.text[:500],
    )
    return None


def _upload_one_photo(token: str, group_id: int, path: str, attempts: int = 3) -> dict:
    """VK иногда молча отдаёт пустой photo при частых подряд загрузках (throttling без явной
    ошибки), а закешированный upload_url может успеть протухнуть — на пустой ответ берём
    свежий upload_url и пробуем ещё раз."""
    upload_url = _wall_upload_url(token, group_id)
    for attempt in range(1, attempts + 1):
        upload_result = _post_photo(upload_url, path)
        if upload_result is not None:
            return upload_result
        if attempt < attempts:
            time.sleep(1.5 * attempt)
            upload_url = _wall_upload_url(token, group_id, refresh=True)
    raise RuntimeError(f"VK upload server returned no photo after {attempts} attempts: {path}")


def _size(path: str) -> int | None:
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def upload_photos(token: str, group_id: int, paths: list[str]) -> list[str]:
    """Грузит фото по одному через photos.getWallUploadServer.

    vk_api.upload.VkUpload.photo_wall шлёт файл в multipart-поле "file0", а этот
    метод VK ждёт поле именно "photo" — с чужим именем сервер отдаёт пустой ответ
    без ошибки, и saveWallPhoto потом падает с "photo is undefined". Поэтому руками.
    """
    if not paths:
        return []
    api = _session(token).get_api()

    attachments = []
    for path in paths:
        upload_result = _upload_one_photo(token, group_id, path)
        saved = _call(api.photos.saveWallPhoto, "photos.saveWallPhoto", group_id=group_id, **upload_result)
        attachments.extend(f"photo{p['owner_id']}_{p['id']}" for p in saved)
        time.sleep(0.5)
    return attachments


def upload_video(token: str, group_id: int, path: str, name: str = "") -> str:
    try:
        item = VkUpload(_session(token)).video(video_file=path, name=name or "video", group_id=group_id)
    except Exception:
        log.exception("VK video upload failed: group_id=%s path=%s size=%s", group_id, path, _size(path))
        raise
    return f"video{item['owner_id']}_{item['video_id']}"


def upload_document(token: str, group_id: int, path: str, name: str) -> str:
    try:
        item = VkUpload(_session(token)).document_wall(doc=path, filename=name, group_id=group_id)
    except Exception:
        log.exception("VK document upload failed: group_id=%s name=%r size=%s", group_id, name, _size(path))
        raise
    doc = item["doc"] if "doc" in item else item
    return f"doc{doc['owner_id']}_{doc['id']}"


def post_to_wall(token: str, group_id: int, message: str, attachments: list[str] | None = None) -> int:
    api = _session(token).get_api()
    params = {"owner_id": -group_id, "from_group": 1, "message": message or ""}
    if attachments:
        params["attachments"] = ",".join(attachments)
    result = _call(api.wall.post, "wall.post", **params)
    post_id = result["post_id"]
    log.info("Posted to VK wall: group_id=%s post_id=%s", group_id, post_id)
    return post_id


def edit_wall_post(token: str, group_id: int, post_id: int, message: str, attachments: list[str] | None = None) -> None:
    api = _session(token).get_api()
    params = {"owner_id": -group_id, "post_id": post_id, "message": message or ""}
    if attachments:
        params["attachments"] = ",".join(attachments)
    _call(api.wall.edit, "wall.edit", **params)
    log.info("Edited VK wall post: group_id=%s post_id=%s", group_id, post_id)
