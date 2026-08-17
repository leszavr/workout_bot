"""Абстракция хранилища профилей.

Application-слой и Telegram gateway не знают, где физически лежат данные.
Сейчас используется ``FileProfileRepository``; позже можно добавить
``PostgresProfileRepository`` без изменения остального кода.
"""
from __future__ import annotations

import abc
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.domain.profile import FitnessProfile
from src.errors import ProfilePersistenceError


class ProfileRepository(abc.ABC):
    @abc.abstractmethod
    def save(self, profile: FitnessProfile) -> FitnessProfile:
        """Сохраняет профиль. Бросает ProfilePersistenceError при ошибке записи."""

    @abc.abstractmethod
    def get(self, profile_id: str) -> FitnessProfile | None: ...

    @abc.abstractmethod
    def exists(self, profile_id: str) -> bool: ...

    @abc.abstractmethod
    def next_display_number(self) -> str:
        """Человекочитаемый номер заявки вида REQ-YYYYMMDD-NNNNN."""

    @abc.abstractmethod
    def delete(self, profile_id: str) -> None:
        """Удаляет профиль (поддержка запроса на удаление данных)."""


class FileProfileRepository(ProfileRepository):
    def __init__(self, profiles_dir: Path, counter_file: Path) -> None:
        self._profiles_dir = profiles_dir
        self._counter_file = counter_file

    def _ensure_dirs(self) -> None:
        self._profiles_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, profile_id: str) -> Path:
        return self._profiles_dir / f"{profile_id}.json"

    def save(self, profile: FitnessProfile) -> FitnessProfile:
        self._ensure_dirs()
        if not profile.profile_id:
            raise ProfilePersistenceError("profile_id is empty")
        profile.touch()
        payload = profile.model_dump(mode="json")
        path = self._path(profile.profile_id)
        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self._profiles_dir), prefix=".tmp-", suffix=".json"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp_name, path)
            except BaseException:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
                raise
        except ProfilePersistenceError:
            raise
        except OSError as exc:
            raise ProfilePersistenceError(f"Не удалось сохранить профиль: {exc}") from exc
        return profile

    def get(self, profile_id: str) -> FitnessProfile | None:
        path = self._path(profile_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return FitnessProfile.model_validate(data)
        except (OSError, json.JSONDecodeError) as exc:
            raise ProfilePersistenceError(f"Не удалось прочитать профиль {profile_id}: {exc}") from exc

    def exists(self, profile_id: str) -> bool:
        return self._path(profile_id).exists()

    def next_display_number(self) -> str:
        self._ensure_dirs()
        counter = 1
        if self._counter_file.exists():
            try:
                data = json.loads(self._counter_file.read_text(encoding="utf-8"))
                counter = int(data.get("value", 1))
            except (TypeError, ValueError, json.JSONDecodeError, OSError):
                counter = 1
        number = f"REQ-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{counter:05d}"
        try:
            self._counter_file.write_text(
                json.dumps({"value": counter + 1}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            raise ProfilePersistenceError(f"Не удалось обновить счётчик заявок: {exc}") from exc
        return number

    def delete(self, profile_id: str) -> None:
        path = self._path(profile_id)
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            raise ProfilePersistenceError(f"Не удалось удалить профиль {profile_id}: {exc}") from exc
