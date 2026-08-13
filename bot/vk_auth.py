from urllib.parse import parse_qs, urlencode, urlparse

VK_OAUTH_BASE_URL = "https://oauth.vk.ru/authorize"
VK_REDIRECT_URI = "https://oauth.vk.ru/blank.html"
VK_SCOPES = "photos,wall"


def oauth_url(client_id: str) -> str:
    return f"{VK_OAUTH_BASE_URL}?{urlencode({
        'client_id': client_id,
        'display': 'page',
        'redirect_uri': VK_REDIRECT_URI,
        'scope': VK_SCOPES,
        'response_type': 'token',
        'v': '5.199',
    })}"


def extract_access_token(value: str) -> str:
    """Принимает отдельный токен либо полный URL после OAuth-редиректа (access_token приходит в fragment)."""
    value = value.strip()
    if not value:
        return ""

    if "://" not in value:
        return value

    parsed = urlparse(value)
    for params in (parse_qs(parsed.fragment), parse_qs(parsed.query)):
        token = params.get("access_token")
        if token and token[0]:
            return token[0]
    return ""
