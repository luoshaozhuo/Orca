"""RedisSource 凭据解析的单元测试，不连接真实 Redis。"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.domain.config import RedisConfig
from infrastructure.redis.source import resolve_password

ENV = "ORCA_TEST_REDIS_PASSWORD"
PASSWORD = "a" * 32
FILE_PASSWORD = "f" * 32


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV, raising=False)


def test_env_password_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, PASSWORD)
    file = tmp_path / "password"
    file.write_text(FILE_PASSWORD + "\n")
    config = RedisConfig(password_env=ENV, password_file=str(file))
    assert resolve_password(config) == PASSWORD


def test_password_file_used_when_env_missing(tmp_path: Path) -> None:
    file = tmp_path / "password"
    file.write_text(FILE_PASSWORD + "\n")
    config = RedisConfig(password_env=ENV, password_file=str(file))
    assert resolve_password(config) == FILE_PASSWORD


def test_empty_env_falls_back_to_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "")
    file = tmp_path / "password"
    file.write_text(FILE_PASSWORD)
    config = RedisConfig(password_env=ENV, password_file=str(file))
    assert resolve_password(config) == FILE_PASSWORD


def test_missing_env_and_file_returns_none(tmp_path: Path) -> None:
    config = RedisConfig(password_env=ENV, password_file=str(tmp_path / "absent"))
    assert resolve_password(config) is None


def test_empty_password_file_returns_none(tmp_path: Path) -> None:
    file = tmp_path / "password"
    file.write_text("")
    config = RedisConfig(password_file=str(file))
    assert resolve_password(config) is None


def test_password_file_trailing_newlines_stripped(tmp_path: Path) -> None:
    file = tmp_path / "password"
    file.write_text(FILE_PASSWORD + "\r\n\n")
    config = RedisConfig(password_file=str(file))
    assert resolve_password(config) == FILE_PASSWORD


def test_password_file_tilde_expanded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".config").mkdir()
    file = home / ".config" / "password"
    file.write_text(FILE_PASSWORD)
    monkeypatch.setenv("HOME", str(home))
    config = RedisConfig(password_file="~/.config/password")
    assert resolve_password(config) == FILE_PASSWORD


def test_no_credentials_configured() -> None:
    assert resolve_password(RedisConfig()) is None
