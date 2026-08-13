import logging

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


def upload_photos(token: str, group_id: int, paths: list[str]) -> list[str]:
    if not paths:
        return []
    items = VkUpload(_session(token)).photo_wall(photos=paths, group_id=group_id)
    return [f"photo{p['owner_id']}_{p['id']}" for p in items]


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
    result = api.wall.post(**params)
    post_id = result["post_id"]
    log.info("Posted to VK wall: group_id=%s post_id=%s", group_id, post_id)
    return post_id
