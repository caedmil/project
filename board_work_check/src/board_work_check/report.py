"""Рендеринг отчёта: человекочитаемый текст и структурированный JSON."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from . import __version__
from .import_detector import mode_reason
from .models import (
    DEFAULT_TIMEZONE,
    ImportMode,
    ParseResult,
    Signals,
    Verdict,
    format_ms_human,
    format_ms_short,
    ms_to_iso,
    plural,
)

VERDICT_LABELS = {
    "work_detected": "работа велась",
    "no_work_detected": "работа не обнаружена",
    "ambiguous": "неоднозначно (нужна ручная проверка)",
}

CONFIDENCE_LABELS = {
    "high": "высокая",
    "medium": "средняя",
    "low": "низкая",
}

VERDICT_COLORS = {
    "work_detected": "32",
    "no_work_detected": "31",
    "ambiguous": "33",
}

_HIDDEN_LIMITATION_PREFIXES = (
    "История до импорта",
    "Режим mixed",
    "Режим доски не определён",
)


def _paint(text: str, code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def _mode_line(import_mode: ImportMode, timezone: str) -> str:
    mode = import_mode.mode
    share = f"{import_mode.top_share * 100:.1f}%"
    if mode == "post_import":
        when = (
            format_ms_human(import_mode.top_created_t, timezone)
            if import_mode.top_created_t is not None
            else "дата неизвестна"
        )
        return (
            f"post_import (импортирована {when}; одинаковый created.t "
            f"у {share} объектов)"
        )
    if mode == "full":
        return (
            "full (created.t выглядит реальным: "
            f"{import_mode.n_unique_created} уникальных значений)"
        )
    if mode == "mixed":
        return (
            "mixed (признаки импорта и ручного создания смешаны: "
            f"самый частый created.t у {share} объектов)"
        )
    return "unknown (нет валидных created.t)"


def _limits_lines(
    parse_result: ParseResult, import_mode: ImportMode, timezone: str
) -> list[str]:
    mode = import_mode.mode
    lines: list[str] = []
    if mode == "post_import" and import_mode.top_created_t is not None:
        lines.append(
            f"История до {format_ms_short(import_mode.top_created_t, timezone)} "
            "недоступна (доска импортирована)"
        )
    elif mode == "mixed":
        lines.append("История до импорта части объектов недоступна")
    elif mode == "unknown":
        lines.append("Время создания объектов неизвестно — режим доски не определён")
    lines.append("Открытие доски без правок не логируется")
    if parse_result.is_truncated:
        lines.append(
            "Файл обрезан: прочитаны не все объекты исходного бэкапа"
        )
    return lines


def _warning_lines(verdict: Verdict) -> list[str]:
    return [
        warning
        for warning in verdict.warnings
        if not warning.startswith(_HIDDEN_LIMITATION_PREFIXES)
    ]


def _weak_spots(signals: Signals, import_mode: ImportMode) -> list[str]:
    items: list[str] = []
    if signals.n_files:
        suffix = (
            " (загружены одним пакетом)"
            if import_mode.mode in ("post_import", "mixed")
            else ""
        )
        items.append(
            f"{signals.n_files} "
            f"{plural(signals.n_files, 'файл', 'файла', 'файлов')} "
            f"(docx/pptx/pdf): вложенные материалы, а не правки на доске{suffix}"
        )
    if signals.n_frames_total and signals.n_frames_without_edits:
        items.append(
            f"На {signals.n_frames_without_edits} из {signals.n_frames_total} "
            "фреймов нет ни одной правки"
        )
    if signals.n_empty_stickers:
        items.append(
            f"{signals.n_empty_stickers} "
            f"{plural(signals.n_empty_stickers, 'стикер', 'стикера', 'стикеров')} "
            "без текста"
        )
    if signals.n_comments == 0:
        items.append("Комментариев нет")
    if import_mode.mode == "mixed":
        items.append("Режим mixed: привязка объектов к времени ненадёжна")
    if not items:
        items.append("Существенных антипаттернов не найдено")
    return items


def render_text_report(
    parse_result: ParseResult,
    import_mode: ImportMode,
    signals: Signals,
    verdict: Verdict,
    *,
    timezone: str = DEFAULT_TIMEZONE,
    color: bool = False,
) -> str:
    """Человекочитаемый отчёт для stdout."""
    lines: list[str] = []
    board_name = parse_result.board_name or "без названия"
    lines.append(f"Доска: {board_name}")
    lines.append(f"Режим: {_mode_line(import_mode, timezone)}")
    covered = f"Покрыто объектов: {signals.n_objects_total}"
    if parse_result.is_truncated:
        covered += " (файл обрезан)"
    lines.append(covered)
    lines.append("")

    verdict_label = VERDICT_LABELS[verdict.verdict]
    colored_verdict = _paint(
        verdict_label, VERDICT_COLORS[verdict.verdict], color
    )
    lines.append(f"ВЕРДИКТ: {colored_verdict}")
    lines.append(f"Уверенность: {CONFIDENCE_LABELS[verdict.confidence]}")
    lines.append("")

    lines.append("Доказательства:")
    if verdict.reasons:
        lines.extend(f"- {reason}" for reason in verdict.reasons)
    else:
        lines.append("- явных сигналов работы не найдено")
    lines.append("")

    lines.append("Слабые места:")
    lines.extend(f"- {item}" for item in _weak_spots(signals, import_mode))
    lines.append("")

    lines.append("Что не видно:")
    lines.extend(f"- {item}" for item in _limits_lines(parse_result, import_mode, timezone))

    warnings = _warning_lines(verdict)
    if warnings:
        lines.append("")
        lines.append("Предупреждения:")
        lines.extend(f"- {item}" for item in warnings)

    lines.append("")
    lines.append(
        f"Порог «настоящей» правки: updated - created > "
        f"{signals.threshold_real_ms / 60_000:g} мин"
    )
    return "\n".join(lines)


def render_json_report(
    parse_result: ParseResult,
    import_mode: ImportMode,
    signals: Signals,
    verdict: Verdict,
    *,
    timezone: str = DEFAULT_TIMEZONE,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Структурированный отчёт (сериализуется в JSON без доработок)."""
    signals_payload = asdict(signals)
    frames_payload = signals_payload.pop("frames_with_edits")

    moment = generated_at or datetime.now(UTC)
    top_created_iso = (
        ms_to_iso(import_mode.top_created_t, timezone)
        if import_mode.top_created_t is not None
        else None
    )

    return {
        "tool": {"name": "board-work-check", "version": __version__},
        "generated_at": moment.isoformat(timespec="seconds"),
        "board": {
            "name": parse_result.board_name,
            "source_path": parse_result.source_path,
            "source_kind": parse_result.source_kind,
            "size_bytes": parse_result.size_bytes,
            "objects_total": signals.n_objects_total,
            "is_truncated": parse_result.is_truncated,
        },
        "import_mode": {
            "mode": import_mode.mode,
            "explanation": mode_reason(import_mode),
            "top_created_t": import_mode.top_created_t,
            "top_created_iso": top_created_iso,
            "top_share": round(import_mode.top_share, 6),
            "n_unique_created": import_mode.n_unique_created,
            "import_time_iso": import_mode.import_time_iso,
        },
        "verdict": {
            "verdict": verdict.verdict,
            "confidence": verdict.confidence,
            "strong_signals": verdict.strong_signals,
            "reasons": list(verdict.reasons),
            "warnings": list(verdict.warnings),
        },
        "signals": signals_payload,
        "frames_with_edits": frames_payload,
        "parser": {
            "is_truncated": parse_result.is_truncated,
            "warnings": list(parse_result.warnings),
        },
        "thresholds": {
            "threshold_real_ms": signals.threshold_real_ms,
            "threshold_real_minutes": signals.threshold_real_ms / 60_000,
        },
    }


def render_lessons_json_report_text(payload: dict[str, Any]) -> str:
    """Сериализовать краткий отчёт по занятиям."""
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_json_report_text(
    parse_result: ParseResult,
    import_mode: ImportMode,
    signals: Signals,
    verdict: Verdict,
    *,
    timezone: str = DEFAULT_TIMEZONE,
) -> str:
    """JSON-отчёт строкой (удобно для stdout и ``--save-json``)."""
    payload = render_json_report(
        parse_result, import_mode, signals, verdict, timezone=timezone
    )
    return json.dumps(payload, ensure_ascii=False, indent=2)
