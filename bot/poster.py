import asyncio
import logging
import tempfile
from pathlib import Path

from telegram import Message
from telegram.ext import ContextTypes

from . import moderation, vk_client

log = logging.getLogger("poster")


def _best_text(messages: list[Message]) -> str:
    for m in messages:
        t = m.text or m.caption
        if t:
            return t
    return ""


async def _download(context: ContextTypes.DEFAULT_TYPE, file_id: str, dest_dir: str, suffix: str) -> str:
    try:
        tg_file = await context.bot.get_file(file_id)
        path = str(Path(dest_dir) / f"{file_id}{suffix}")
        await tg_file.download_to_drive(path)
    except Exception:
        log.exception("Telegram download failed: file_id=%s suffix=%s", file_id, suffix)
        raise
    return path


class EmptyPostError(RuntimeError):
    """В посте нет ни текста, ни медиа, которые бот умеет перенести в VK.

    Без этой проверки VK отвечает невнятным "[100] invalid message param".
    """


def _describe_kinds(messages: list[Message]) -> str:
    kinds = {type(m.effective_attachment).__name__ for m in messages if m.effective_attachment is not None}
    return ", ".join(sorted(kinds)) or "пустое сообщение"


async def _build_post(messages: list[Message], context: ContextTypes.DEFAULT_TYPE, token: str, group_id: int) -> tuple[str, list[str]]:
    """Скачивает медиа из одного или нескольких TG-сообщений (пост или альбом), грузит в VK. Возвращает (текст, attachments).

    Сначала всё скачивает и проверяет фото на 16+, и только потом грузит в VK: иначе
    видео из заблокированного поста успело бы осесть в видеозаписях группы.
    """
    text = _best_text(messages)

    with tempfile.TemporaryDirectory() as tmp:
        photo_paths: list[str] = []
        other_uploads: list[tuple] = []  # (функция загрузки в VK, её аргументы)

        for m in messages:
            if m.photo:
                photo_paths.append(await _download(context, m.photo[-1].file_id, tmp, ".jpg"))
            elif m.video:
                path = await _download(context, m.video.file_id, tmp, ".mp4")
                other_uploads.append((vk_client.upload_video, token, group_id, path, text[:100] or "video"))
            elif m.animation:
                path = await _download(context, m.animation.file_id, tmp, ".mp4")
                other_uploads.append((vk_client.upload_video, token, group_id, path, text[:100] or "gif"))
            elif m.video_note:
                path = await _download(context, m.video_note.file_id, tmp, ".mp4")
                other_uploads.append((vk_client.upload_video, token, group_id, path, text[:100] or "video"))
            elif m.document:
                doc = m.document
                suffix = Path(doc.file_name or "file").suffix or ".bin"
                path = await _download(context, doc.file_id, tmp, suffix)
                if doc.mime_type and doc.mime_type.startswith("image/"):
                    photo_paths.append(path)
                else:
                    other_uploads.append((vk_client.upload_document, token, group_id, path, doc.file_name or "file"))

        if not text.strip() and not photo_paths and not other_uploads:
            raise EmptyPostError(
                f"в посте нет ни текста, ни медиа, которые можно перенести в VK (в Telegram: {_describe_kinds(messages)})"
            )

        await asyncio.to_thread(moderation.check_photos, photo_paths)

        other_attachments = [await asyncio.to_thread(*upload) for upload in other_uploads]
        photo_attachments = await asyncio.to_thread(vk_client.upload_photos, token, group_id, photo_paths) if photo_paths else []
        attachments = photo_attachments + other_attachments

    return text, attachments


async def post_messages(messages: list[Message], context: ContextTypes.DEFAULT_TYPE, token: str, group_id: int) -> int:
    text, attachments = await _build_post(messages, context, token, group_id)
    return await asyncio.to_thread(vk_client.post_to_wall, token, group_id, text, attachments)


async def edit_post(
    messages: list[Message], context: ContextTypes.DEFAULT_TYPE, token: str, group_id: int, vk_post_id: int
) -> None:
    """Пересобирает пост (текст + медиа) из отредактированного TG-сообщения и правит существующий пост в VK."""
    text, attachments = await _build_post(messages, context, token, group_id)
    await asyncio.to_thread(vk_client.edit_wall_post, token, group_id, vk_post_id, text, attachments)
