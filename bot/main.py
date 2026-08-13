import asyncio
import logging
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from . import config, poster, storage, vk_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")

ASK_TOKEN, ASK_GROUP_ID = range(2)

# альбомы, пришедшие форвардом в личку: "user_id:media_group_id" -> {"messages","context","user_id","task"}
_pending_forward_albums: dict[str, dict] = {}
# альбомы из привязанного канала: "chat_id:media_group_id" -> {"messages","context","group","task"}
_pending_channel_albums: dict[str, dict] = {}
# ожидание выбора VK-группы кнопкой: token -> {"messages","context"}
_pending_choices: dict[str, dict] = {}


async def _dm(context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str, **kwargs) -> None:
    try:
        await context.bot.send_message(user_id, text, **kwargs)
    except Exception:
        log.warning("Не смог написать юзеру %s в личку (не жал /start боту?)", user_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    groups = storage.list_groups(user_id)
    pending = storage.pending_channels(user_id)

    lines = [
        "Репощу посты из Telegram в VK.",
        "",
        "Чтобы репостить новые посты канала — добавь меня админом в канал.",
        "Чтобы закинуть старый пост — просто перешли его мне сюда.",
    ]
    if groups:
        lines.append("")
        lines.append("Твои VK-группы:")
        for g in groups.values():
            lines.append(f"• {g['label']}")

    buttons = [[InlineKeyboardButton("➕ Добавить VK-группу", callback_data="addgroup")]]
    for cid, ch in pending.items():
        buttons.append([InlineKeyboardButton(f"🔗 Привязать канал «{ch['title']}»", callback_data=f"linkstart:{cid}")])

    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))


VKTOKEN_HELP = (
    "VK не даёт заливать фото/видео/документы на стену сообщества по простому ключу доступа группы — "
    "только по токену пользователя-админа этого сообщества. Как получить:\n\n"
    "1. https://vk.com/apps?act=manage -> Создать приложение -> тип «Standalone-приложение». Скопируй ID приложения.\n"
    "2. Собери ссылку, подставив свой ID вместо ID_ПРИЛОЖЕНИЯ:\n"
    "https://oauth.vk.com/authorize?client_id=ID_ПРИЛОЖЕНИЯ&display=page&redirect_uri=https://oauth.vk.com/blank.html"
    "&scope=wall,photos,video,docs,groups,offline&response_type=token&v=5.199\n"
    "3. Открой её в браузере под тем VK-аккаунтом, который админ нужного сообщества, разреши доступ.\n"
    "4. После редиректа в адресной строке будет что-то вроде "
    "blank.html#access_token=ДЛИННАЯ_СТРОКА&expires_in=0&user_id=...\n"
    "   Скопируй значение access_token — это и есть токен для бота."
)


async def vktoken_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(VKTOKEN_HELP, disable_web_page_preview=True)


async def add_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "Пришли VK user-токен с правами wall,photos,video,docs,groups (обычный ключ доступа сообщества "
        "для фото/видео не подходит — так велит сам VK API). Как получить — /vktoken."
    )
    return ASK_TOKEN


async def add_group_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["vk_token"] = update.message.text.strip()
    await update.message.reply_text("Теперь пришли id группы (число, без минуса).")
    return ASK_GROUP_ID


async def add_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    token = context.user_data.pop("vk_token", None)
    raw = update.message.text.strip()
    if not raw.isdigit():
        await update.message.reply_text("Это не похоже на число. Пришли id группы ещё раз.")
        return ASK_GROUP_ID

    group_id = int(raw)
    try:
        info = await asyncio.to_thread(vk_client.group_info, token, group_id)
    except Exception as exc:
        log.warning("VK group check failed: %s", exc)
        await update.message.reply_text(
            "Не получилось проверить токен/id группы. Проверь права токена (стена, фото, видео, документы) и начни заново: /start"
        )
        return ConversationHandler.END

    label = info.get("name", f"VK группа {group_id}")
    storage.add_group(update.effective_user.id, token, group_id, label)
    await update.message.reply_text(f"Готово, добавил «{label}».")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменил.")
    return ConversationHandler.END


async def _send_to_vk(messages: list[Message], context: ContextTypes.DEFAULT_TYPE, group: dict, user_id: int) -> None:
    try:
        vk_post_id = await poster.post_messages(messages, context, group["token"], group["group_id"])
        await _dm(context, user_id, f"Отправлено в «{group['label']}» (post_id={vk_post_id}).")
    except Exception:
        log.exception("Failed to post forwarded messages")
        await _dm(context, user_id, "Не получилось отправить в VK, глянь логи на сервере.")


async def _resolve_and_post(messages: list[Message], context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    groups = storage.list_groups(user_id)
    if not groups:
        await _dm(context, user_id, "Сначала добавь VK-группу: /start")
        return

    if len(groups) == 1:
        group = next(iter(groups.values()))
        await _send_to_vk(messages, context, group, user_id)
        return

    token = uuid.uuid4().hex
    _pending_choices[token] = {"messages": messages, "context": context}
    buttons = [[InlineKeyboardButton(g["label"], callback_data=f"pick:{token}:{key}")] for key, g in groups.items()]
    await _dm(context, user_id, "В какую VK-группу закинуть?", reply_markup=InlineKeyboardMarkup(buttons))


async def _flush_forward_album(key: str) -> None:
    await asyncio.sleep(config.ALBUM_COLLECT_DELAY)
    bundle = _pending_forward_albums.pop(key, None)
    if not bundle:
        return
    await _resolve_and_post(bundle["messages"], bundle["context"], bundle["user_id"])


async def on_forwarded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user_id = update.effective_user.id

    if not message.media_group_id:
        await _resolve_and_post([message], context, user_id)
        return

    key = f"{user_id}:{message.media_group_id}"
    bundle = _pending_forward_albums.get(key)
    if bundle is None:
        bundle = {"messages": [], "context": context, "user_id": user_id, "task": None}
        _pending_forward_albums[key] = bundle
    bundle["messages"].append(message)
    if bundle["task"]:
        bundle["task"].cancel()
    bundle["task"] = asyncio.create_task(_flush_forward_album(key))


async def on_group_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, token, group_key = query.data.split(":", 2)
    bundle = _pending_choices.pop(token, None)
    if not bundle:
        await query.edit_message_text("Эта кнопка уже неактуальна, перешли пост заново.")
        return
    group = storage.get_group(update.effective_user.id, group_key)
    if not group:
        await query.edit_message_text("Группа не найдена (удалена?).")
        return
    await query.edit_message_text(f"Отправляю в «{group['label']}»…")
    await _send_to_vk(bundle["messages"], bundle["context"], group, update.effective_user.id)


async def on_start_linkchan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, chat_id_str = query.data.split(":", 1)
    user_id = update.effective_user.id
    groups = storage.list_groups(user_id)
    if not groups:
        await query.edit_message_text("Сначала добавь VK-группу: /start")
        return
    buttons = [[InlineKeyboardButton(g["label"], callback_data=f"linkpick:{chat_id_str}:{key}")] for key, g in groups.items()]
    await query.edit_message_text("В какую VK-группу привязать канал?", reply_markup=InlineKeyboardMarkup(buttons))


async def on_link_channel_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, chat_id_str, group_key = query.data.split(":", 2)
    chat_id = int(chat_id_str)
    user_id = update.effective_user.id
    group = storage.get_group(user_id, group_key)
    channel = storage.get_channel(chat_id)
    if not group or not channel:
        await query.edit_message_text("Не найдено, попробуй заново.")
        return
    storage.link_channel(chat_id, user_id, group_key, channel["title"])
    await query.edit_message_text(f"Канал «{channel['title']}» привязан к «{group['label']}».")


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cmu = update.my_chat_member
    chat = cmu.chat
    if chat.type != "channel":
        return
    if cmu.new_chat_member.status not in ("administrator", "member"):
        return  # бота убрали/понизили - игнор

    user_id = cmu.from_user.id
    title = chat.title or str(chat.id)
    groups = storage.list_groups(user_id)

    if not groups:
        storage.register_channel_pending(chat.id, user_id, title)
        await _dm(context, user_id, f"Добавил в канал «{title}». Сначала добавь VK-группу (/start), потом привяжу канал к ней.")
        return

    if len(groups) == 1:
        group_key, group = next(iter(groups.items()))
        storage.link_channel(chat.id, user_id, group_key, title)
        await _dm(context, user_id, f"Канал «{title}» привязан к «{group['label']}». Новые посты будут репоститься автоматически.")
        return

    storage.register_channel_pending(chat.id, user_id, title)
    buttons = [[InlineKeyboardButton(g["label"], callback_data=f"linkpick:{chat.id}:{key}")] for key, g in groups.items()]
    await _dm(context, user_id, f"Добавил в канал «{title}». В какую VK-группу репостить?", reply_markup=InlineKeyboardMarkup(buttons))


async def _flush_channel_album(key: str) -> None:
    await asyncio.sleep(config.ALBUM_COLLECT_DELAY)
    bundle = _pending_channel_albums.pop(key, None)
    if not bundle:
        return
    try:
        await poster.post_messages(bundle["messages"], bundle["context"], bundle["group"]["token"], bundle["group"]["group_id"])
    except Exception:
        log.exception("Failed to post channel album %s", key)


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.channel_post
    if message is None:
        return

    channel = storage.get_channel(message.chat_id)
    if not channel or not channel.get("group_key"):
        return
    group = storage.get_group(channel["user_id"], channel["group_key"])
    if not group:
        return

    if message.media_group_id:
        key = f"{message.chat_id}:{message.media_group_id}"
        bundle = _pending_channel_albums.get(key)
        if bundle is None:
            bundle = {"messages": [], "context": context, "group": group, "task": None}
            _pending_channel_albums[key] = bundle
        bundle["messages"].append(message)
        if bundle["task"]:
            bundle["task"].cancel()
        bundle["task"] = asyncio.create_task(_flush_channel_album(key))
        return

    try:
        await poster.post_messages([message], context, group["token"], group["group_id"])
    except Exception:
        log.exception("Failed to process channel post %s", message.message_id)


def build_application() -> Application:
    app = Application.builder().token(config.TG_BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_group_start, pattern="^addgroup$")],
        states={
            ASK_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_group_token)],
            ASK_GROUP_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_group_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("vktoken", vktoken_help))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(on_group_pick, pattern="^pick:"))
    app.add_handler(CallbackQueryHandler(on_start_linkchan, pattern="^linkstart:"))
    app.add_handler(CallbackQueryHandler(on_link_channel_pick, pattern="^linkpick:"))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, on_channel_post))
    app.add_handler(MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, on_forwarded))
    return app


def main() -> None:
    app = build_application()
    log.info("Bot started (multi-tenant mode)")
    app.run_polling(allowed_updates=["channel_post", "message", "callback_query", "my_chat_member"])


if __name__ == "__main__":
    main()
