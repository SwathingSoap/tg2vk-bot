from urllib.parse import parse_qs, urlencode, urlparse

import requests

VK_OAUTH_BASE_URL = "https://oauth.vk.ru/authorize"
VK_TOKEN_URL = "https://oauth.vk.ru/access_token"
VK_REDIRECT_URI = "https://oauth.vk.ru/blank.html"
VK_SCOPES = "photos,wall,offline"


def oauth_url(client_id: str) -> str:
    return f"{VK_OAUTH_BASE_URL}?{urlencode({
        'client_id': client_id,
        'display': 'page',
        'redirect_uri': VK_REDIRECT_URI,
        'scope': VK_SCOPES,
        'response_type': 'code',
        'v': '5.199',
    })}"


def extract_code(value: str) -> str:
    """Принимает отдельный код либо полный URL после OAuth-редиректа (code приходит в query, не в fragment)."""
    value = value.strip()
    if not value:
        return ""

    if "://" not in value:
        return value

    parsed = urlparse(value)
    for params in (parse_qs(parsed.query), parse_qs(parsed.fragment)):
        code = params.get("code")
        if code and code[0]:
            return code[0]
    return ""


def exchange_code(client_id: str, client_secret: str, code: str) -> str:
    """Меняет authorization code на постоянный (offline, без привязки к IP) access_token."""
    resp = requests.get(
        VK_TOKEN_URL,
        params={
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": VK_REDIRECT_URI,
            "code": code,
        },
        timeout=15,
    )
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(data.get("error_description") or data.get("error") or "unknown VK OAuth error")
    return token
