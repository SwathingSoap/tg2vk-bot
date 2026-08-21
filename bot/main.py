import asyncio
import itertools
import logging
import uuid
from dataclasses import dataclass

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
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

from . import config, poster, storage, vk_auth, vk_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# httpx пишет секретный Telegram bot token прямо в URL каждого Bot API запроса.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

ASK_TOKEN, ASK_GROUP_ID = range(2)

VKHOST_URL = "https://vkhost.github.io/"

# альбомы, пришедшие форвардом в личку: "user_id:media_group_id" -> {"messages","context","user_id","task"}
_pending_forward_albums: dict[str, dict] = {}
# альбомы из привязанного канала: "chat_id:media_group_id" -> {"messages","context","group","task"}
_pending_channel_albums: dict[str, dict] = {}
# ожидание выбора VK-группы кнопкой: token -> {"messages","context"}
_pending_choices: dict[str, dict] = {}
# одиночные (не альбомные) посты канала: "chat_id:message_id" -> id поста в VK.
# None, пока пост ещё не улетел в VK (и channel_post, и edited_channel_post на публикацию
# отложенного поста могут прийти на одно и то же сообщение — второй раз просто игнорим).
# Когда id уже есть — следующий edited_channel_post на этот же message_id это правка
# в Telegram, и мы правим тот же пост в VK, а не создаём новый.
_channel_posts: dict[str, int | None] = {}
_NOT_SEEN = object()  # маркер отсутствия ключа в _channel_posts, отличимый от значения None
# альбомные сообщения канала, которые уже обработали: "chat_id:message_id" — правки внутри
# альбома не поддерживаем (Telegram шлёт только изменившееся сообщение, не весь альбом),
# поэтому повторные edited_channel_post на уже запощенный альбом просто игнорим.
_seen_album_messages: set[str] = set()


@dataclass
class PostJob:
    """Один логический пост (одиночное сообщение или альбом), готовый к публикации в VK."""

    messages: list[Message]
    context: ContextTypes.DEFAULT_TYPE
    token: str
    group_id: int
    group_label: str
    status_chat_id: int | None = None
    status_message_id: int | None = None
    channel_owner_id: int | None = None
    channel_title: str | None = None
    post_identity: str | None = None
    edit_vk_post_id: int | None = None


_seq_counter = itertools.count()
# приоритет по номеру прибытия — иначе одиночные посты (встают в очередь сразу)
# обгоняют альбомы (у них 1.5с debounce перед постановкой), хотя пришли позже.
_post_queue: "asyncio.PriorityQueue[tuple[int, PostJob]]" = asyncio.PriorityQueue()


async def _dm(context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str, **kwargs) -> None:
    try:
        await context.bot.send_message(user_id, text, **kwargs)
    except Exception:
        log.warning("Не смог написать юзеру %s в личку (не жал /start боту?)", user_id)


async def _reply(update: Update, text: str, **kwargs) -> None:
    """Отвечает и на команду, и на нажатие кнопки — во втором случае редактирует то же сообщение."""
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, **kwargs)
    else:
        await update.effective_message.reply_text(text, **kwargs)


def _start_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    groups = storage.list_groups(user_id)
    pending = storage.pending_channels(user_id)

    lines = [
        "Репощу посты из Telegram в VK.",
        "",
        "Чтобы репостить новые посты канала — добавь меня админом в канал.",
        "Чтобы закинуть старый пост — просто перешли его мне сюда.",
        "",
        "Команды: /addgroup — добавить VK-группу, /groups — управление группами, /vktoken — как получить токен.",
    ]
    if groups:
        lines.append("")
        lines.append("Твои VK-группы:")
        for g in groups.values():
            lines.append(f"• {g['label']}")

    buttons = [[InlineKeyboardButton("➕ Добавить VK-группу", callback_data="addgroup")]]
    if groups:
        buttons.append([InlineKeyboardButton("🗑 Управлять группами", callback_data="groups")])
    for cid, ch in pending.items():
        buttons.append([InlineKeyboardButton(f"🔗 Привязать канал «{ch['title']}»", callback_data=f"linkstart:{cid}")])

    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, markup = _start_view(update.effective_user.id)
    await _reply(update, text, reply_markup=markup)


async def on_back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def groups_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    groups = storage.list_groups(user_id)
    if not groups:
        await _reply(update, "Групп пока нет. Добавь через /addgroup.")
        return

    buttons = [[InlineKeyboardButton(f"❌ {g['label']}", callback_data=f"delgroup:{key}")] for key, g in groups.items()]
    buttons.append([InlineKeyboardButton("‹ Назад", callback_data="backstart")])
    await _reply(update, "Твои VK-группы. Нажми, чтобы удалить:", reply_markup=InlineKeyboardMarkup(buttons))


async def on_delete_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, group_key = query.data.split(":", 1)
    user_id = update.effective_user.id

    group = storage.get_group(user_id, group_key)
    if not group:
        await query.edit_message_text("Уже удалено.")
        return

    unlinked = storage.remove_group(user_id, group_key)
    text = f"Удалил «{group['label']}»."
    if unlinked:
        text += "\n\nЭти каналы остались без привязки (перепривяжи через /start): " + ", ".join(unlinked)

    groups = storage.list_groups(user_id)
    buttons = [[InlineKeyboardButton(f"❌ {g['label']}", callback_data=f"delgroup:{key}")] for key, g in groups.items()]
    buttons.append([InlineKeyboardButton("‹ Назад", callback_data="backstart")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


VKTOKEN_HELP = (
    "1. Открой vkhost.github.io (кнопка ниже) под тем VK-аккаунтом, который админ нужного сообщества.\n"
    "2. Выбери тип «Сообщество», укажи id группы.\n"
    "3. Отметь права: стена, фото, видео, документы, управление сообществами.\n"
    "4. Нажми «Получить», разреши доступ.\n"
    "5. Пришли сюда ссылку из адресной строки после редиректа (или сам access_token) — "
    "бот вытащит токен и сразу удалит сообщение."
)


def _vk_token_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔐 Получить VK-токен на vkhost.github.io", url=VKHOST_URL)]])


async def vktoken_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(VKTOKEN_HELP, reply_markup=_vk_token_keyboard(), disable_web_page_preview=True)


async def add_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _reply(update, VKTOKEN_HELP, reply_markup=_vk_token_keyboard())
    return ASK_TOKEN


async def add_group_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text
    token = vk_auth.extract_access_token(raw)
    try:
        await update.message.delete()
    except Exception:
        log.warning("Не удалось удалить сообщение с VK-токеном пользователя %s", update.effective_user.id)

    if not token:
        await update.effective_chat.send_message(
            "Не нашёл access_token. Пришли всю ссылку из адресной строки после авторизации или сам токен."
        )
        return ASK_TOKEN

    context.user_data["vk_token"] = token
    await update.effective_chat.send_message("Токен получил и удалил из чата. Теперь пришли id группы (число, без минуса).")
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
        await asyncio.to_thread(vk_client.check_wall_photo_upload, token, group_id)
    except Exception as exc:
        log.warning("VK token/group capability check failed: %s", exc)
        await update.message.reply_text(
            f"VK не разрешил загрузку фото в эту группу: {exc}\n\n"
            "Проверь, что ты админ этой группы и id указан верно, и начни заново: /addgroup"
        )
        return ConversationHandler.END

    label = info.get("name", f"VK группа {group_id}")
    storage.add_group(update.effective_user.id, token, group_id, label)
    await update.message.reply_text(f"Готово, добавил «{label}».")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменил.")
    return ConversationHandler.END


async def _queue_worker() -> None:
    while True:
        _seq, job = await _post_queue.get()
        try:
            await _process_job(job)
        except Exception:
            log.exception("Не удалось обработать пост из очереди")
        finally:
            _post_queue.task_done()


async def _set_status(job: PostJob, text: str) -> None:
    if job.status_chat_id is None or job.status_message_id is None:
        return
    try:
        await job.context.bot.edit_message_text(text, chat_id=job.status_chat_id, message_id=job.status_message_id)
    except Exception:
        pass


async def _process_job(job: PostJob) -> None:
    is_edit = job.edit_vk_post_id is not None
    await _set_status(job, f"⏳ {'Обновляю' if is_edit else 'Загружаю'} в «{job.group_label}»…")
    try:
        if is_edit:
            await poster.edit_post(job.messages, job.context, job.token, job.group_id, job.edit_vk_post_id)
            vk_post_id = job.edit_vk_post_id
        else:
            vk_post_id = await poster.post_messages(job.messages, job.context, job.token, job.group_id)
    except Exception as exc:
        log.exception("Failed to post job")
        if is_edit and job.post_identity is not None:
            _channel_posts[job.post_identity] = job.edit_vk_post_id  # правка не удалась, пост в VK остаётся прежним
        elif job.post_identity is not None:
            _channel_posts.pop(job.post_identity, None)  # публикация не удалась, дадим попробовать заново
        if "too big" in str(exc).lower():
            text = f"❌ Не отправлено в «{job.group_label}»: файл больше 20 МБ — лимит Telegram Bot API на скачивание, тут не обойти."
        else:
            text = f"❌ Не получилось отправить в «{job.group_label}», глянь логи на сервере."
        await _set_status(job, text)
        return

    if job.post_identity is not None:
        _channel_posts[job.post_identity] = vk_post_id

    link = f"https://vk.com/wall-{job.group_id}_{vk_post_id}"
    await _set_status(job, f"{'✏️ Обновлено' if is_edit else '✅ Опубликовано'} в «{job.group_label}»\n{link}")

    if job.channel_owner_id is not None:
        verb = "обновлён" if is_edit else "опубликован"
        await _dm(
            job.context, job.channel_owner_id,
            f"Пост из канала «{job.channel_title}» {verb} в «{job.group_label}»\n{link}",
        )


async def _enqueue(
    seq: int,
    messages: list[Message],
    context: ContextTypes.DEFAULT_TYPE,
    group: dict,
    user_id: int,
    status_chat_id: int | None = None,
    status_message_id: int | None = None,
) -> None:
    if status_message_id is None:
        try:
            msg = await context.bot.send_message(user_id, "🕐 В очереди…")
            status_chat_id, status_message_id = msg.chat_id, msg.message_id
        except Exception:
            log.warning("Не смог написать юзеру %s в личку (не жал /start боту?)", user_id)
            status_chat_id = status_message_id = None

    job = PostJob(
        messages=messages,
        context=context,
        token=group["token"],
        group_id=group["group_id"],
        group_label=group["label"],
        status_chat_id=status_chat_id,
        status_message_id=status_message_id,
    )
    position = _post_queue.qsize()
    if position:
        await _set_status(job, f"🕐 В очереди ({position} перед тобой)…")
    await _post_queue.put((seq, job))


async def _resolve_and_post(seq: int, messages: list[Message], context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    groups = storage.list_groups(user_id)
    if not groups:
        await _dm(context, user_id, "Сначала добавь VK-группу: /addgroup")
        return

    if len(groups) == 1:
        group = next(iter(groups.values()))
        await _enqueue(seq, messages, context, group, user_id)
        return

    token = uuid.uuid4().hex
    _pending_choices[token] = {"seq": seq, "messages": messages, "context": context}
    buttons = [[InlineKeyboardButton(g["label"], callback_data=f"pick:{token}:{key}")] for key, g in groups.items()]
    await _dm(context, user_id, "В какую VK-группу закинуть?", reply_markup=InlineKeyboardMarkup(buttons))


async def _flush_forward_album(key: str) -> None:
    await asyncio.sleep(config.ALBUM_COLLECT_DELAY)
    bundle = _pending_forward_albums.pop(key, None)
    if not bundle:
        return
    await _resolve_and_post(bundle["seq"], bundle["messages"], bundle["context"], bundle["user_id"])


async def on_forwarded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user_id = update.effective_user.id

    if not message.media_group_id:
        await _resolve_and_post(next(_seq_counter), [message], context, user_id)
        return

    key = f"{user_id}:{message.media_group_id}"
    bundle = _pending_forward_albums.get(key)
    if bundle is None:
        bundle = {"seq": next(_seq_counter), "messages": [], "context": context, "user_id": user_id, "task": None}
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
    await query.edit_message_text("🕐 В очереди…")
    await _enqueue(
        bundle["seq"], bundle["messages"], bundle["context"], group, update.effective_user.id,
        status_chat_id=query.message.chat_id, status_message_id=query.message.message_id,
    )


async def on_start_linkchan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, chat_id_str = query.data.split(":", 1)
    user_id = update.effective_user.id
    groups = storage.list_groups(user_id)
    if not groups:
        await query.edit_message_text("Сначала добавь VK-группу: /addgroup")
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
        await _dm(context, user_id, f"Добавил в канал «{title}». Сначала добавь VK-группу (/addgroup), потом привяжу канал к ней.")
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
    group = bundle["group"]
    job = PostJob(
        messages=bundle["messages"], context=bundle["context"],
        token=group["token"], group_id=group["group_id"], group_label=group["label"],
        channel_owner_id=bundle["owner_id"], channel_title=bundle["channel_title"],
    )
    await _post_queue.put((bundle["seq"], job))


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # отложенный ("запланированный") пост при публикации приходит от Telegram как
    # edited_channel_post, а не channel_post — ловим оба типа.
    message = update.channel_post or update.edited_channel_post
    if message is None:
        return

    channel = storage.get_channel(message.chat_id)
    if not channel or not channel.get("group_key"):
        return
    group = storage.get_group(channel["user_id"], channel["group_key"])
    if not group:
        return

    if message.media_group_id:
        member_key = f"{message.chat_id}:{message.message_id}"
        if member_key in _seen_album_messages:
            return  # уже запощен в составе альбома, правки альбомов не поддерживаем
        _seen_album_messages.add(member_key)

        key = f"{message.chat_id}:{message.media_group_id}"
        bundle = _pending_channel_albums.get(key)
        if bundle is None:
            bundle = {
                "seq": next(_seq_counter), "messages": [], "context": context, "group": group, "task": None,
                "owner_id": channel["user_id"], "channel_title": channel["title"],
            }
            _pending_channel_albums[key] = bundle
        bundle["messages"].append(message)
        if bundle["task"]:
            bundle["task"].cancel()
        bundle["task"] = asyncio.create_task(_flush_channel_album(key))
        return

    identity = f"{message.chat_id}:{message.message_id}"
    existing = _channel_posts.get(identity, _NOT_SEEN)
    if existing is None:
        return  # публикация этого же поста уже в очереди/в процессе — не дублируем
    if existing is _NOT_SEEN:
        _channel_posts[identity] = None  # помечаем как "в процессе", пока не улетит в VK
        edit_vk_post_id = None
    else:
        edit_vk_post_id = existing  # уже опубликован — это правка

    job = PostJob(
        messages=[message], context=context, token=group["token"], group_id=group["group_id"], group_label=group["label"],
        channel_owner_id=channel["user_id"], channel_title=channel["title"],
        post_identity=identity, edit_vk_post_id=edit_vk_post_id,
    )
    await _post_queue.put((next(_seq_counter), job))


def build_application() -> Application:
    async def _post_init(app: Application) -> None:
        await app.bot.set_my_commands([
            BotCommand("start", "Главное меню"),
            BotCommand("addgroup", "Добавить VK-группу"),
            BotCommand("groups", "Управление VK-группами"),
            BotCommand("vktoken", "Как получить VK-токен"),
        ])
        asyncio.create_task(_queue_worker())

    app = Application.builder().token(config.TG_BOT_TOKEN).post_init(_post_init).build()

    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_group_start, pattern="^addgroup$"),
            CommandHandler("addgroup", add_group_start),
        ],
        states={
            ASK_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_group_token)],
            ASK_GROUP_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_group_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("vktoken", vktoken_help))
    app.add_handler(CommandHandler("groups", groups_menu))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(on_group_pick, pattern="^pick:"))
    app.add_handler(CallbackQueryHandler(on_start_linkchan, pattern="^linkstart:"))
    app.add_handler(CallbackQueryHandler(on_link_channel_pick, pattern="^linkpick:"))
    app.add_handler(CallbackQueryHandler(groups_menu, pattern="^groups$"))
    app.add_handler(CallbackQueryHandler(on_delete_group, pattern="^delgroup:"))
    app.add_handler(CallbackQueryHandler(on_back_to_start, pattern="^backstart$"))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(
        MessageHandler(filters.UpdateType.CHANNEL_POST | filters.UpdateType.EDITED_CHANNEL_POST, on_channel_post)
    )
    app.add_handler(MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, on_forwarded))
    return app


def main() -> None:
    app = build_application()
    log.info("Bot started (multi-tenant mode)")
    app.run_polling(
        allowed_updates=["channel_post", "edited_channel_post", "message", "callback_query", "my_chat_member"]
    )


if __name__ == "__main__":
    main()
