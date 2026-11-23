"""Доменные модели инструмента ``board-work-check``.

Модуль намеренно не зависит от парсера/CLI: здесь только структуры данных
и хелперы для работы с миллисекундными Unix-таймстампами из бэкапа доски.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ImportModeName = Literal["full", "post_import", "mixed", "unknown"]
VerdictName = Literal["work_detected", "no_work_detected", "ambiguous"]
ConfidenceName = Literal["high", "medium", "low"]

DEFAULT_TIMEZONE = "Asia/Yekaterinburg"
MS_IN_HOUR = 3_600_000.0
MS_IN_MINUTE = 60_000.0


def resolve_timezone(name: str | None) -> tuple[tzinfo, str]:
    """Вернуть (tzinfo, фактическое имя).

    Если в системе нет базы IANA (частая ситуация на Windows без ``tzdata``),
    откатываемся на UTC, чтобы инструмент не падал из-за оформления отчёта.
    """
    if not name:
        return UTC, "UTC"
    try:
        return ZoneInfo(name), name
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return UTC, "UTC"


def ms_to_datetime(ms: float, tz_name: str = DEFAULT_TIMEZONE) -> datetime:
    """Миллисекунды UTC -> aware datetime в указанной таймзоне."""
    tz, _ = resolve_timezone(tz_name)
    return datetime.fromtimestamp(ms / 1000.0, tz=tz)


def ms_to_iso(ms: float, tz_name: str = DEFAULT_TIMEZONE) -> str:
    """ISO-8601 с offset, например ``2026-10-14T13:30:47+05:00``."""
    return ms_to_datetime(ms, tz_name).isoformat(timespec="seconds")


def format_ms_short(ms: float, tz_name: str = DEFAULT_TIMEZONE) -> str:
    """``14.10.2026 13:30`` — для пунктов обоснования."""
    return ms_to_datetime(ms, tz_name).strftime("%d.%m.%Y %H:%M")


def format_ms_human(ms: float, tz_name: str = DEFAULT_TIMEZONE) -> str:
    """``14.10.2026 13:30 Asia/Yekaterinburg`` — для строки режима."""
    _, effective_name = resolve_timezone(tz_name)
    return f"{format_ms_short(ms, tz_name)} {effective_name}"


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русская форма слова для числа ``n``: 1 объект / 2 объекта / 5 объектов."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_delta_hours(hours: float | None) -> str:
    """Человекочитаемая длительность для обоснований."""
    if hours is None:
        return "н/д"
    if hours < 1.0:
        return f"{hours * 60:.0f} мин"
    if hours < 48.0:
        return f"{hours:.1f} ч"
    return f"{hours / 24:.1f} сут"


@dataclass(slots=True)
class ParsedObject:
    """Один объект доски, приведённый к плоскому виду.

    Поля ``created_a``/``updated_a`` (номера авторов) хранятся раздельно:
    автор создания и автор последней правки могут быть разными людьми.
    """

    object_id: str
    type: str
    parent_id: str | None = None
    label: str | None = None
    created_a: int | None = None
    created_t: float | None = None
    updated_a: int | None = None
    updated_t: float | None = None
    bounds: dict[str, Any] | None = None
    text: str | None = None
    # Расширение под реальный формат: имя картинки/файла для эвристики
    # «фото/скриншот решения, а не схема».
    file_name: str | None = None

    @property
    def is_updated(self) -> bool:
        return self.updated_t is not None

    def edit_delta_ms(self) -> float | None:
        """updated.t - created.t в миллисекундах, либо None."""
        if self.created_t is None or self.updated_t is None:
            return None
        return self.updated_t - self.created_t

    def is_real_edit(self, threshold_ms: int) -> bool:
        """Правка спустя ``threshold_ms`` после создания/импорта."""
        delta = self.edit_delta_ms()
        return delta is not None and delta > threshold_ms


@dataclass(slots=True)
class FrameWithEdits:
    """Фрейм, внутри которого есть хотя бы одна «настоящая» правка."""

    frame_id: str
    label: str | None = None
    last_edit_iso: str | None = None
    delta_hours: float | None = None
    edited_object_ids: list[str] = field(default_factory=list)
    last_edit_t: float | None = None


@dataclass(slots=True)
class ParseResult:
    """Результат разбора одного бэкапа."""

    board_name: str
    objects: list[ParsedObject]
    comments_count: int
    is_truncated: bool
    warnings: list[str] = field(default_factory=list)
    source_path: str | None = None
    source_kind: str = "json"  # "json" | "zip"
    size_bytes: int = 0


@dataclass(slots=True)
class ImportMode:
    """Режим доски, выведенный из распределения ``created.t``."""

    mode: ImportModeName
    top_created_t: float | None = None
    top_share: float = 0.0
    n_unique_created: int = 0
    import_time_iso: str | None = None


@dataclass(slots=True)
class Signals:
    """Все посчитанные сигналы по доске."""

    n_objects_total: int
    n_objects_by_type: dict[str, int]
    n_objects_with_text: int
    n_objects_updated: int
    n_real_edits: int
    n_empty_stickers: int
    n_stickers_with_text: int
    n_filled_table_cells: int
    n_drawings: int
    n_mind_map_nodes_with_text: int
    n_images: int
    n_files: int
    n_comments: int
    n_unique_updated_authors: int
    created_spread_hours: float | None
    frames_with_edits: list[FrameWithEdits]
    # --- расширения (нужны отчёту, добавлены в конец со значениями по умолчанию)
    n_frames_total: int = 0
    n_frames_without_edits: int = 0
    n_images_photo_like: int = 0
    n_stickers_total: int = 0
    n_objects_created_same_ms: int = 0
    threshold_real_ms: int = 300_000


@dataclass(slots=True)
class Verdict:
    """Вердикт и его обоснование."""

    verdict: VerdictName
    confidence: ConfidenceName
    reasons: list[str]
    warnings: list[str]
    strong_signals: int = 0


def hours_between(start_ms: float, end_ms: float) -> float:
    return (end_ms - start_ms) / MS_IN_HOUR


def minutes_to_ms(minutes: float) -> int:
    return int(round(minutes * MS_IN_MINUTE))


def ms_to_timedelta(ms: float) -> timedelta:
    return timedelta(milliseconds=ms)
