"""Загрузка и валидация ``config.yaml``.

Значения по умолчанию живут в коде, файл — необязательный override.
Валидацию выполняет pydantic, поэтому опечатка в ключе или отрицательный
порог падают с понятным сообщением, а не тихо меняют логику.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import DEFAULT_TIMEZONE, minutes_to_ms, resolve_timezone

DEFAULT_SUSPICIOUS_IMAGE_PATTERNS: Final[tuple[str, ...]] = (
    r"screenshot",
    r"screen[\s_-]?shot",
    r"\bскрин",
    r"\bснимок",
    r"\bфото",
    r"photo",
    r"\bimg[\s_-]?\d",
    r"\bdsc[\s_-]?\d",
    r"whatsapp",
    r"telegram",
    r"\bscan\b",
    r"\bскан",
    r"решение",
    r"solution",
    r"ответ",
)


class ConfigError(ValueError):
    """Конфигурация не может быть загружена или невалидна."""


class ClassificationConfig(BaseModel):
    """Пороги, влияющие на вердикт и уверенность."""

    model_config = ConfigDict(extra="forbid")

    # Минимум «настоящих» правок для вердикта work_detected.
    n_during_min: int = Field(default=3, ge=1)
    # Сколько сильных сигналов нужно для уверенности high.
    confidence_high_min_signals: int = Field(default=3, ge=1)
    # Начиная с какого числа warnings уверенность падает до low.
    warnings_low_threshold: int = Field(default=3, ge=2)


class AnalysisConfig(BaseModel):
    """Параметры анализа доски."""

    model_config = ConfigDict(extra="forbid")

    threshold_real_minutes: float = Field(default=5.0, gt=0)
    import_share_threshold: float = Field(default=0.9, gt=0, le=1)
    full_share_threshold: float = Field(default=0.3, gt=0, le=1)
    unique_created_min: int = Field(default=5, ge=1)
    timezone: str = DEFAULT_TIMEZONE
    classification: ClassificationConfig = Field(default_factory=ClassificationConfig)
    # Регэкспы (без учёта регистра) имён картинок, похожих на фото/скрин решения.
    suspicious_image_name_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SUSPICIOUS_IMAGE_PATTERNS)
    )

    @field_validator("suspicious_image_name_patterns")
    @classmethod
    def _check_patterns(cls, value: list[str]) -> list[str]:
        compiled: list[str] = []
        for pattern in value:
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:  # pragma: no cover - защита от плохого конфига
                raise ValueError(f"некорректный регэксп {pattern!r}: {exc}") from exc
            compiled.append(pattern)
        return compiled

    @model_validator(mode="after")
    def _check_threshold_order(self) -> AnalysisConfig:
        if self.full_share_threshold >= self.import_share_threshold:
            raise ValueError(
                "full_share_threshold должен быть меньше import_share_threshold"
            )
        return self


class Config(BaseModel):
    """Корневой конфиг: сейчас только секция ``analysis``."""

    model_config = ConfigDict(extra="forbid")

    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)

    # ------------------------------------------------------------------ утилиты

    @property
    def threshold_real_ms(self) -> int:
        return minutes_to_ms(self.analysis.threshold_real_minutes)

    @property
    def threshold_real_minutes(self) -> float:
        return self.analysis.threshold_real_minutes

    @property
    def n_during_min(self) -> int:
        return self.analysis.classification.n_during_min

    @property
    def timezone_name(self) -> str:
        return self.analysis.timezone

    def tz(self) -> Any:
        """tzinfo для форматирования дат (UTC, если базы IANA нет)."""
        tzinfo, _ = resolve_timezone(self.analysis.timezone)
        return tzinfo

    def timezone_is_available(self) -> bool:
        _, effective = resolve_timezone(self.analysis.timezone)
        return effective == self.analysis.timezone

    def with_threshold_minutes(self, minutes: float) -> Config:
        """Копия конфига с переопределённым порогом «настоящей» правки."""
        if minutes <= 0:
            raise ConfigError("порог --threshold-real-minutes должен быть больше нуля")
        clone = self.model_copy(deep=True)
        clone.analysis.threshold_real_minutes = float(minutes)
        return clone

    # ------------------------------------------------------------------ загрузка

    @classmethod
    def defaults(cls) -> Config:
        return cls()

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Прочитать YAML-конфиг; ``None`` — значения по умолчанию."""
        if path is None:
            return cls.defaults()
        path = Path(path)
        if not path.exists():
            raise ConfigError(f"файл конфигурации не найден: {path}")
        try:
            raw_text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise ConfigError(f"не удалось прочитать {path}: {exc}") from exc
        if not raw_text.strip():
            return cls.defaults()
        try:
            payload = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            raise ConfigError(f"невалидный YAML в {path}: {exc}") from exc
        if payload is None:
            return cls.defaults()
        if not isinstance(payload, dict):
            raise ConfigError(f"корень {path} должен быть словарём")
        try:
            return cls.model_validate(payload)
        except Exception as exc:  # pydantic.ValidationError
            raise ConfigError(f"невалидная конфигурация {path}: {exc}") from exc
