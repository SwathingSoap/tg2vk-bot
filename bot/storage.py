import json
from pathlib import Path

from . import config

_path = Path(config.STORAGE_PATH)


def _load() -> dict:
    if not _path.exists():
        return {"users": {}, "channels": {}}
    return json.loads(_path.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    _path.parent.mkdir(parents=True, exist_ok=True)
    _path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def add_group(user_id: int, token: str, group_id: int, label: str) -> str:
    data = _load()
    user = data["users"].setdefault(str(user_id), {"groups": {}})
    key = str(group_id)
    user["groups"][key] = {"token": token, "group_id": group_id, "label": label}
    _save(data)
    return key


def list_groups(user_id: int) -> dict:
    return _load()["users"].get(str(user_id), {}).get("groups", {})


def get_group(user_id: int, group_key: str) -> dict | None:
    return list_groups(user_id).get(group_key)


def remove_group(user_id: int, group_key: str) -> list[str]:
    """Удаляет группу, отвязывает от неё каналы. Возвращает названия отвязанных каналов."""
    data = _load()
    user = data["users"].get(str(user_id))
    if not user or group_key not in user["groups"]:
        return []
    user["groups"].pop(group_key)

    unlinked = []
    for ch in data["channels"].values():
        if ch["user_id"] == user_id and ch["group_key"] == group_key:
            ch["group_key"] = None
            unlinked.append(ch["title"])

    _save(data)
    return unlinked


def register_channel_pending(channel_id: int, user_id: int, title: str) -> None:
    """Канал добавлен (бот стал админом), но ещё не привязан к VK-группе."""
    data = _load()
    data["channels"].setdefault(str(channel_id), {"user_id": user_id, "group_key": None, "title": title})
    _save(data)


def link_channel(channel_id: int, user_id: int, group_key: str, title: str) -> None:
    data = _load()
    data["channels"][str(channel_id)] = {"user_id": user_id, "group_key": group_key, "title": title}
    _save(data)


def get_channel(channel_id: int) -> dict | None:
    return _load()["channels"].get(str(channel_id))


def pending_channels(user_id: int) -> dict:
    data = _load()
    return {cid: c for cid, c in data["channels"].items() if c["user_id"] == user_id and c["group_key"] is None}
