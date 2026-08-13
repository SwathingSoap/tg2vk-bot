import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Env var {name} is not set")
    return value


TG_BOT_TOKEN = _require("TG_BOT_TOKEN")
TG_SOURCE_CHAT_ID = int(_require("TG_SOURCE_CHAT_ID"))

VK_GROUP_TOKEN = _require("VK_GROUP_TOKEN")
VK_GROUP_ID = int(_require("VK_GROUP_ID"))

ALBUM_COLLECT_DELAY = float(os.getenv("ALBUM_COLLECT_DELAY", "1.5"))
