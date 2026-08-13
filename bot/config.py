import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Env var {name} is not set")
    return value


TG_BOT_TOKEN = _require("TG_BOT_TOKEN")
VK_CLIENT_ID = os.getenv("VK_CLIENT_ID", "54719873").strip()
ALBUM_COLLECT_DELAY = float(os.getenv("ALBUM_COLLECT_DELAY", "1.5").split("#")[0].strip())
STORAGE_PATH = os.getenv("STORAGE_PATH", "data/storage.json")
