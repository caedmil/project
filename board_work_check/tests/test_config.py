"""Тесты загрузки и валидации конфигурации."""

from __future__ import annotations

from pathlib import Path

import pytest

from board_work_check.config import Config, ConfigError


def test_defaults_match_spec() -> None:
    config = Config.defaults()

    assert config.threshold_real_ms == 300_000
    assert config.threshold_real_minutes == 5.0
    assert config.timezone_name == "Asia/Yekaterinburg"
    assert config.n_during_min == 3
    assert config.analysis.import_share_threshold == 0.9
    assert config.analysis.full_share_threshold == 0.3
    assert config.analysis.unique_created_min == 5


def test_yaml_overrides_defaults(workdir: Path) -> None:
    path = workdir / "config.yaml"
    path.write_text(
        "analysis:\n"
        "  threshold_real_minutes: 2.5\n"
        "  timezone: UTC\n"
        "  classification:\n"
        "    n_during_min: 4\n",
        encoding="utf-8",
    )

    config = Config.load(path)

    assert config.threshold_real_ms == 150_000
    assert config.timezone_name == "UTC"
    assert config.n_during_min == 4


def test_empty_file_gives_defaults(workdir: Path) -> None:
    path = workdir / "config.yaml"
    path.write_text("", encoding="utf-8")

    assert Config.load(path) == Config.defaults()


def test_missing_file_raises(workdir: Path) -> None:
    with pytest.raises(ConfigError):
        Config.load(workdir / "absent.yaml")


def test_invalid_yaml_raises(workdir: Path) -> None:
    path = workdir / "config.yaml"
    path.write_text("analysis: [не закрыт", encoding="utf-8")

    with pytest.raises(ConfigError):
        Config.load(path)


def test_unknown_key_raises(workdir: Path) -> None:
    path = workdir / "config.yaml"
    path.write_text("analysis:\n  unknown_key: 1\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        Config.load(path)


def test_non_mapping_root_raises(workdir: Path) -> None:
    path = workdir / "config.yaml"
    path.write_text("- 1\n- 2\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        Config.load(path)


def test_negative_threshold_raises(workdir: Path) -> None:
    path = workdir / "config.yaml"
    path.write_text("analysis:\n  threshold_real_minutes: -1\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        Config.load(path)


def test_threshold_order_is_validated(workdir: Path) -> None:
    path = workdir / "config.yaml"
    path.write_text(
        "analysis:\n"
        "  full_share_threshold: 0.95\n"
        "  import_share_threshold: 0.9\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        Config.load(path)


def test_with_threshold_minutes_returns_copy() -> None:
    config = Config.defaults()

    changed = config.with_threshold_minutes(10)

    assert changed.threshold_real_ms == 600_000
    assert config.threshold_real_ms == 300_000


def test_with_threshold_minutes_rejects_non_positive() -> None:
    with pytest.raises(ConfigError):
        Config.defaults().with_threshold_minutes(0)


def test_timezone_is_available() -> None:
    assert Config.defaults().timezone_is_available() is True
