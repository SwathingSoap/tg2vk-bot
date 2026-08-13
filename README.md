# Telegram -> VK repost bot

Бот слушает пост в Telegram-канале и дублирует его на стену группы ВКонтакте: текст, эмодзи, фото (в том числе альбомы), видео, гифки и документы.

## Как это работает

- Бот добавлен админом в Telegram-канал и получает событие `channel_post` на каждый новый пост.
- Фото/видео/документы скачиваются во временную папку, загружаются в VK через `photos.getWallUploadServer` / `video.save` / `docs.getWallUploadServer`, затем публикуется `wall.post` от имени группы.
- Альбомы (несколько фото в одном посте) прилетают отдельными сообщениями с общим `media_group_id` — бот их собирает в течение `ALBUM_COLLECT_DELAY` секунд и публикует одним постом.
- Форматирование текста (жирный/курсив/ссылки) у ВК на стене не поддерживается — публикуется как обычный текст, эмодзи копируются как есть (это обычные unicode-символы).

## 1. Создать Telegram-бота

1. Написать [@BotFather](https://t.me/BotFather) -> `/newbot`, получить токен -> `TG_BOT_TOKEN`.
2. Добавить бота **администратором** в канал-источник (право "Публикация сообщений" не нужно, только доступ к чтению постов).
3. Узнать `chat_id` канала — например переслать любое сообщение из канала боту [@userinfobot](https://t.me/userinfobot) или добавить бота в канал и посмотреть апдейт через `https://api.telegram.org/bot<TOKEN>/getUpdates` после любого нового поста. Значение вида `-100xxxxxxxxxx` -> `TG_SOURCE_CHAT_ID`.

## 2. Получить токен VK-сообщества

1. В группе ВК: **Управление -> Работа с API -> Ключи доступа -> Создать ключ**.
2. Выдать права: **стена, фото, видео, документы**.
3. Скопировать токен -> `VK_GROUP_TOKEN`.
4. `VK_GROUP_ID` — числовой id группы (без минуса), смотри в URL группы или в том же разделе API.

## 3. Настроить .env

```bash
cp .env.example .env
# заполнить TG_BOT_TOKEN, TG_SOURCE_CHAT_ID, VK_GROUP_TOKEN, VK_GROUP_ID
```

## 4. Запуск локально (проверка)

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt   # Windows: venv\Scripts\pip install -r requirements.txt
venv/bin/python -m bot.main                # Windows: venv\Scripts\python -m bot.main
```

Кинуть тестовый пост с фото и текстом в канал — он должен появиться на стене VK-группы.

## 5. Деплой на VPS через GitHub Actions

### Первичный сетап VPS (один раз)

На чистом Ubuntu/Debian VPS от root:

```bash
curl -fsSL https://raw.githubusercontent.com/<ТВОЙ_ЮЗЕР>/<ТВОЙ_РЕПО>/main/deploy/setup_vps.sh -o setup_vps.sh
bash setup_vps.sh https://github.com/<ТВОЙ_ЮЗЕР>/<ТВОЙ_РЕПО>.git
```

Скрипт: ставит python3/venv/git, создаёт системного юзера `botuser`, клонирует репо в `/opt/telegram-vk-bot`, ставит venv и зависимости, копирует `deploy/telegram-vk-bot.service` в systemd.

После этого:

```bash
nano /opt/telegram-vk-bot/.env   # заполнить реальными токенами
systemctl start telegram-vk-bot
systemctl status telegram-vk-bot
journalctl -u telegram-vk-bot -f  # логи
```

### Разрешить деплою рестартовать сервис без пароля

```bash
echo "botuser ALL=(root) NOPASSWD: /bin/systemctl restart telegram-vk-bot, /bin/systemctl status telegram-vk-bot" > /etc/sudoers.d/telegram-vk-bot
```

(если деплой по SSH идёт под тем же `botuser`, что владеет `/opt/telegram-vk-bot` — рекомендуется).

### Секреты в GitHub

В репозитории: **Settings -> Secrets and variables -> Actions -> New repository secret**:

| Secret | Значение |
|---|---|
| `VPS_HOST` | IP или домен VPS |
| `VPS_USER` | пользователь для SSH (например `botuser`) |
| `VPS_SSH_KEY` | приватный SSH-ключ (без пароля) для этого юзера |
| `VPS_PORT` | обычно `22` |

Публичный ключ от этой пары должен быть в `~/.ssh/authorized_keys` у `VPS_USER` на VPS.

### Как деплоится дальше

Любой `git push` в `main` триггерит `.github/workflows/deploy.yml`: заходит по SSH, `git pull`, ставит зависимости, рестартует `systemctl restart telegram-vk-bot`. Токены (`.env`) на VPS руками не трогаются деплоем — только код.

## Ограничения

- Видео в VK после `video.save` обрабатывается асинхронно на стороне VK — сразу после публикации поста видео может показывать "обрабатывается" пару минут.
- Кастомные emoji (Premium Telegram) публикуются как их emoji-заглушка (обычный unicode), анимированная версия недоступна — ограничение самого VK.
- Редактирование/удаление поста в Telegram сейчас не синхронизируется с VK (бот реагирует только на новые посты).
