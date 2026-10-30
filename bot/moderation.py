import logging
from pathlib import Path

import requests

from . import config

log = logging.getLogger("moderation")

API_URL = "https://api.sightengine.com/1.0/check.json"

# Классы модели nudity-2.1, на которых пост не пускаем. Скоры у Sightengine инклюзивные
# (картинка уровня erotica тянет вверх и suggestive), но берём максимум по всем, чтобы
# не зависеть от этого. mildly_suggestive (мини-юбки, шорты) сознательно не блокируем.
_BLOCK_CLASSES = {
    "sexual_activity": "сексуальная активность",
    "sexual_display": "откровенная нагота",
    "erotica": "эротика",
    "very_suggestive": "сильно провокационное",
    "suggestive": "провокационное (бельё, позы, нагота в арте)",
}


class ModerationError(RuntimeError):
    """Sightengine не смог проверить картинку (ключи, лимит тарифа, битый файл)."""


class ModerationBlocked(RuntimeError):
    """Картинка похожа на 16+ — пост в VK не пускаем."""

    def __init__(self, index: int, label: str, score: float):
        self.index = index
        self.label = label
        self.score = score
        super().__init__(f"картинка {index}: {label} ({score:.0%})")


def enabled() -> bool:
    return bool(config.SIGHTENGINE_API_USER and config.SIGHTENGINE_API_SECRET)


def _check(path: str) -> dict:
    with Path(path).open("rb") as f:
        resp = requests.post(
            API_URL,
            files={"media": f},
            data={
                "models": "nudity-2.1",
                "api_user": config.SIGHTENGINE_API_USER,
                "api_secret": config.SIGHTENGINE_API_SECRET,
            },
            timeout=60,
        )
    try:
        result = resp.json()
    except ValueError:
        raise ModerationError(f"Sightengine вернул не JSON: HTTP {resp.status_code} {resp.text[:300]}") from None
    if result.get("status") != "success":
        error = result.get("error") or {}
        raise ModerationError(f"Sightengine: {error.get('message') or resp.text[:300]}")
    return result["nudity"]


def check_photos(paths: list[str]) -> None:
    """Проверяет фото по очереди и бросает ModerationBlocked на первом 16+.

    Без ключей Sightengine проверка выключена и всё проходит как раньше.
    """
    if not enabled():
        return
    for index, path in enumerate(paths, start=1):
        nudity = _check(path)
        cls, score = max(((c, float(nudity.get(c, 0))) for c in _BLOCK_CLASSES), key=lambda item: item[1])
        log.info("Moderation: photo %s/%s worst=%s score=%.2f", index, len(paths), cls, score)
        if score >= config.MODERATION_THRESHOLD:
            raise ModerationBlocked(index, _BLOCK_CLASSES[cls], score)
