import asyncio
import logging
import tempfile
from pathlib import Path

from telegram import Message, Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from . import config, vk_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")

# media_group_id -> {"messages": [Message, ...], "task": asyncio.Task}
_pending_albums: dict[str, dict] = {}


def _best_text(msg: Message) -> str:
    return msg.text or msg.caption or ""


async def _download(context: ContextTypes.DEFAULT_TYPE, file_id: str, dest_dir: str, suffix: str) -> str:
    tg_file = await context.bot.get_file(file_id)
    path = str(Path(dest_dir) / f"{file_id}{suffix}")
    await tg_file.download_to_drive(path)
    return path


async def _process_single(message: Message, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a message that is not part of an album."""
    with tempfile.TemporaryDirectory() as tmp:
        attachments: list[str] = []
        text = _best_text(message)

        if message.photo:
            path = await _download(context, message.photo[-1].file_id, tmp, ".jpg")
            attachments = await asyncio.to_thread(vk_client.upload_photos, [path])
        elif message.video:
            path = await _download(context, message.video.file_id, tmp, ".mp4")
            att = await asyncio.to_thread(vk_client.upload_video, path, text[:100] or "video")
            attachments = [att]
        elif message.animation:
            path = await _download(context, message.animation.file_id, tmp, ".mp4")
            att = await asyncio.to_thread(vk_client.upload_video, path, text[:100] or "gif")
            attachments = [att]
        elif message.document:
            doc = message.document
            suffix = Path(doc.file_name or "file").suffix or ".bin"
            path = await _download(context, doc.file_id, tmp, suffix)
            if doc.mime_type and doc.mime_type.startswith("image/"):
                attachments = await asyncio.to_thread(vk_client.upload_photos, [path])
            else:
                att = await asyncio.to_thread(vk_client.upload_document, path, doc.file_name or "file")
                attachments = [att]
        elif not text:
            log.warning("Unsupported message type (id=%s), skipping", message.message_id)
            return

        await asyncio.to_thread(vk_client.post_to_wall, text, attachments)


async def _flush_album(media_group_id: str) -> None:
    await asyncio.sleep(config.ALBUM_COLLECT_DELAY)
    bundle = _pending_albums.pop(media_group_id, None)
    if not bundle:
        return
    messages: list[Message] = bundle["messages"]
    context: ContextTypes.DEFAULT_TYPE = bundle["context"]

    messages.sort(key=lambda m: m.message_id)
    text = ""
    for m in messages:
        t = _best_text(m)
        if t:
            text = t
            break

    with tempfile.TemporaryDirectory() as tmp:
        photo_paths: list[str] = []
        other_attachments: list[str] = []

        for m in messages:
            if m.photo:
                path = await _download(context, m.photo[-1].file_id, tmp, ".jpg")
                photo_paths.append(path)
            elif m.video:
                path = await _download(context, m.video.file_id, tmp, ".mp4")
                att = await asyncio.to_thread(vk_client.upload_video, path, text[:100] or "video")
                other_attachments.append(att)
            elif m.document:
                doc = m.document
                suffix = Path(doc.file_name or "file").suffix or ".bin"
                path = await _download(context, doc.file_id, tmp, suffix)
                att = await asyncio.to_thread(vk_client.upload_document, path, doc.file_name or "file")
                other_attachments.append(att)

        photo_attachments = await asyncio.to_thread(vk_client.upload_photos, photo_paths) if photo_paths else []
        attachments = photo_attachments + other_attachments
        await asyncio.to_thread(vk_client.post_to_wall, text, attachments)


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.channel_post
    if message is None or message.chat_id != config.TG_SOURCE_CHAT_ID:
        return

    if message.media_group_id:
        gid = message.media_group_id
        bundle = _pending_albums.get(gid)
        if bundle is None:
            bundle = {"messages": [], "context": context, "task": None}
            _pending_albums[gid] = bundle
        bundle["messages"].append(message)
        if bundle["task"]:
            bundle["task"].cancel()
        bundle["task"] = asyncio.create_task(_flush_album(gid))
        return

    try:
        await _process_single(message, context)
    except Exception:
        log.exception("Failed to process message %s", message.message_id)


def main() -> None:
    app = Application.builder().token(config.TG_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, on_channel_post))
    log.info("Bot started, watching chat_id=%s", config.TG_SOURCE_CHAT_ID)
    app.run_polling(allowed_updates=["channel_post"])


if __name__ == "__main__":
    main()
