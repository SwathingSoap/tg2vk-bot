import logging

import vk_api
from vk_api.upload import VkUpload

from . import config

log = logging.getLogger("vk_client")

_session = vk_api.VkApi(token=config.VK_GROUP_TOKEN, api_version="5.199")
_api = _session.get_api()
_upload = VkUpload(_session)


def upload_photos(paths: list[str]) -> list[str]:
    """Upload photos for a wall post, return list of attachment strings."""
    if not paths:
        return []
    items = _upload.photo_wall(photos=paths, group_id=config.VK_GROUP_ID)
    return [f"photo{p['owner_id']}_{p['id']}" for p in items]


def upload_video(path: str, name: str = "") -> str:
    item = _upload.video(
        video_file=path,
        name=name or "video",
        group_id=config.VK_GROUP_ID,
    )
    return f"video{item['owner_id']}_{item['video_id']}"


def upload_document(path: str, name: str) -> str:
    item = _upload.document_wall(
        doc=path,
        filename=name,
        group_id=config.VK_GROUP_ID,
    )
    doc = item["doc"] if "doc" in item else item
    return f"doc{doc['owner_id']}_{doc['id']}"


def post_to_wall(message: str, attachments: list[str] | None = None) -> int:
    params = {
        "owner_id": -config.VK_GROUP_ID,
        "from_group": 1,
        "message": message or "",
    }
    if attachments:
        params["attachments"] = ",".join(attachments)
    result = _api.wall.post(**params)
    post_id = result["post_id"]
    log.info("Posted to VK wall: post_id=%s", post_id)
    return post_id
