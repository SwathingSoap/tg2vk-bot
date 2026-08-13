import asyncio
import tempfile
from pathlib import Path

from telegram import Message
from telegram.ext import ContextTypes

from . import vk_client


def _best_text(messages: list[Message]) -> str:
    for m in messages:
        t = m.text or m.caption
        if t:
            return t
    return ""


async def _download(context: ContextTypes.DEFAULT_TYPE, file_id: str, dest_dir: str, suffix: str) -> str:
    tg_file = await context.bot.get_file(file_id)
    path = str(Path(dest_dir) / f"{file_id}{suffix}")
    await tg_file.download_to_drive(path)
    return path


async def post_messages(messages: list[Message], context: ContextTypes.DEFAULT_TYPE, token: str, group_id: int) -> int:
    """Скачивает медиа из одного или нескольких TG-сообщений (пост или альбом) и публикует одним постом в VK."""
    text = _best_text(messages)

    with tempfile.TemporaryDirectory() as tmp:
        photo_paths: list[str] = []
        other_attachments: list[str] = []

        for m in messages:
            if m.photo:
                path = await _download(context, m.photo[-1].file_id, tmp, ".jpg")
                photo_paths.append(path)
            elif m.video:
                path = await _download(context, m.video.file_id, tmp, ".mp4")
                att = await asyncio.to_thread(vk_client.upload_video, token, group_id, path, text[:100] or "video")
                other_attachments.append(att)
            elif m.animation:
                path = await _download(context, m.animation.file_id, tmp, ".mp4")
                att = await asyncio.to_thread(vk_client.upload_video, token, group_id, path, text[:100] or "gif")
                other_attachments.append(att)
            elif m.video_note:
                path = await _download(context, m.video_note.file_id, tmp, ".mp4")
                att = await asyncio.to_thread(vk_client.upload_video, token, group_id, path, text[:100] or "video")
                other_attachments.append(att)
            elif m.document:
                doc = m.document
                suffix = Path(doc.file_name or "file").suffix or ".bin"
                path = await _download(context, doc.file_id, tmp, suffix)
                if doc.mime_type and doc.mime_type.startswith("image/"):
                    photo_paths.append(path)
                else:
                    att = await asyncio.to_thread(vk_client.upload_document, token, group_id, path, doc.file_name or "file")
                    other_attachments.append(att)

        photo_attachments = await asyncio.to_thread(vk_client.upload_photos, token, group_id, photo_paths) if photo_paths else []
        attachments = photo_attachments + other_attachments

    return await asyncio.to_thread(vk_client.post_to_wall, token, group_id, text, attachments)
