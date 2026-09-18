import os

from dotenv import load_dotenv

load_dotenv()


def _clean(value: str) -> str:
    """Срезает inline-комментарий и пробелы.

    python-dotenv оставляет "# ..." в значении, если оно не в кавычках, — из-за этого
    бот однажды падал в рестарт-луп на int() от id канала с хвостом комментария.
    """
    return value.split("#")[0].strip()


def _require(name: str) -> str:
    value = _clean(os.getenv(name) or "")
    if not value:
        raise RuntimeError(f"Env var {name} is not set")
    return value


def _optional(name: str, default: str) -> str:
    return _clean(os.getenv(name) or "") or default


TG_BOT_TOKEN = _require("TG_BOT_TOKEN")
ALBUM_COLLECT_DELAY = float(_optional("ALBUM_COLLECT_DELAY", "1.5"))
STORAGE_PATH = _optional("STORAGE_PATH", "data/storage.json")
