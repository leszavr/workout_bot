from __future__ import annotations

import json
from datetime import datetime, timezone

from config import COUNTER_FILE, DATA_DIR, LOGS_DIR, PROFILES_DIR
from services.profile_builder import normalize_profile


def ensure_dirs() -> None:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_counter() -> int:
    ensure_dirs()
    if not COUNTER_FILE.exists():
        return 1
    try:
        data = json.loads(COUNTER_FILE.read_text(encoding="utf-8"))
        return int(data.get("value", 1))
    except (TypeError, ValueError):
        return 1


def save_counter(value: int) -> None:
    ensure_dirs()
    COUNTER_FILE.write_text(json.dumps({"value": value}, ensure_ascii=False, indent=2), encoding="utf-8")


def next_profile_id() -> str:
    counter = load_counter()
    profile_id = f"REQ-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{counter:05d}"
    save_counter(counter + 1)
    return profile_id


def save_profile(profile: dict) -> str:
    ensure_dirs()
    profile = normalize_profile(profile)
    profile_id = profile.get("profile_id") or next_profile_id()
    profile["profile_id"] = profile_id
    path = PROFILES_DIR / f"{profile_id}.json"
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def log_user_response(profile_id: str, user_id: int, message: str) -> None:
    ensure_dirs()
    log_path = LOGS_DIR / f"{profile_id}.log"
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] user_id={user_id} message={message}\n")
