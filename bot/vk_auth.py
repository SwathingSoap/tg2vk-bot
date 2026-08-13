from urllib.parse import parse_qs, urlparse


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
