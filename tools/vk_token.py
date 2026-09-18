"""Получение пользовательского VK-токена через VK ID (OAuth 2.1 + PKCE).

vkhost и прочие сторонние выдавалки работают под чужими app_id, которые VK
методично прикрывает — токены оттуда рано или поздно начинают отвечать
[9] Flood control на любой метод, включая users.get. Свой app_id от этого не
зависит.

Как пользоваться:

1. Создай приложение на https://id.vk.com/about/business (или dev.vk.com),
   тип "Веб-сайт" либо "Standalone".
2. В настройках приложения пропиши доверенный redirect URL. Подойдёт любой
   https-адрес, на который ты сможешь посмотреть в адресной строке, например
   https://oauth.vk.com/blank.html
3. Запусти: python tools/vk_token.py <CLIENT_ID> <REDIRECT_URI>
4. Открой напечатанную ссылку, войди, разреши доступ.
5. Скопируй адрес из адресной строки после редиректа и вставь сюда.
6. Скрипт обменяет code на access_token и напечатает его.
7. Токен скорми боту: /addgroup.

Эндпоинты VK ID менялись; если обмен упадёт, сверь URL и имена параметров с
https://id.vk.com/docs и поправь константы ниже.
"""

import base64
import hashlib
import os
import secrets
import sys
from urllib.parse import parse_qs, urlparse

import requests

AUTHORIZE_URL = "https://id.vk.ru/authorize"
TOKEN_URL = "https://id.vk.ru/oauth2/auth"
SCOPE = "wall,photos,video,docs,groups,offline"


def _pkce_pair() -> tuple[str, str]:
    """code_verifier и его S256-challenge, оба в base64url без паддинга."""
    verifier = base64.urlsafe_b64encode(os.urandom(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def _extract(redirect_url: str, name: str) -> str | None:
    values = parse_qs(urlparse(redirect_url).query).get(name)
    return values[0] if values else None


def main() -> int:
    # На Windows консоль по умолчанию не UTF-8, и кириллица в print падает.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if len(sys.argv) != 3:
        print(f"Использование: python {sys.argv[0]} <CLIENT_ID> <REDIRECT_URI>")
        return 2

    client_id, redirect_uri = sys.argv[1], sys.argv[2]
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = AUTHORIZE_URL + "?" + "&".join(f"{k}={requests.utils.quote(v, safe='')}" for k, v in params.items())

    print("\n1) Открой эту ссылку в браузере, войди и разреши доступ:\n")
    print(url)
    print("\n2) После редиректа скопируй адрес целиком из адресной строки.\n")
    redirect_url = input("Вставь адрес: ").strip()

    code = _extract(redirect_url, "code")
    if not code:
        error = _extract(redirect_url, "error_description") or _extract(redirect_url, "error")
        print(f"\nВ адресе нет code. {error or 'Проверь, что скопировал весь URL.'}")
        return 1

    returned_state = _extract(redirect_url, "state")
    if returned_state != state:
        print("\nstate не совпал — ссылка не от этого запуска. Начни заново.")
        return 1

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
    }
    device_id = _extract(redirect_url, "device_id")
    if device_id:
        payload["device_id"] = device_id

    resp = requests.post(TOKEN_URL, data=payload, timeout=30)
    try:
        data = resp.json()
    except ValueError:
        print(f"\nVK ID ответил не JSON (status {resp.status_code}):\n{resp.text[:1000]}")
        return 1

    token = data.get("access_token")
    if not token:
        print(f"\nТокен не выдан (status {resp.status_code}):\n{data}")
        print("\nЕсли ругается на параметры — сверь их с https://id.vk.com/docs и поправь скрипт.")
        return 1

    print("\nГотово. access_token:\n")
    print(token)
    expires = data.get("expires_in")
    print(f"\nЖивёт: {'бессрочно (offline)' if not expires else str(expires) + ' сек'}")
    print("Проверь его командой /vkcheck после того, как добавишь группу через /addgroup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
