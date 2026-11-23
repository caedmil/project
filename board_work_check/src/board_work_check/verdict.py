"""Правила вердикта и уверенности.

Только детерминированные правила по посчитанным числам. Никакой
интерпретации смысла текста — исключительно наличие/количество.
"""

from __future__ import annotations

from datetime import datetime

from .config import Config
from .models import (
    FrameWithEdits,
    ImportMode,
    Signals,
    Verdict,
    format_delta_hours,
    format_ms_human,
    plural,
)


def _minutes_text(threshold_ms: int) -> str:
    return f"{threshold_ms / 60_000:g}"


def _edit_phrase(mode: str) -> str:
    if mode == "post_import":
        return "после импорта"
    if mode == "full":
        return "после создания"
    return "после создания/импорта"


def _frame_when(frame: FrameWithEdits) -> str:
    if not frame.last_edit_iso:
        return "н/д"
    try:
        return datetime.fromisoformat(frame.last_edit_iso).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return frame.last_edit_iso


def _count_strong_signals(signals: Signals) -> int:
    """Сколько «сильных» сигналов присутствует (для правила confidence)."""
    strong = 0
    if signals.n_real_edits > 0:
        strong += 1
    if signals.frames_with_edits:
        strong += 1
    if signals.n_unique_updated_authors > 0:
        strong += 1
    if signals.n_comments > 0:
        strong += 1
    if signals.created_spread_hours is not None and signals.created_spread_hours > 24:
        strong += 1
    return strong


def compute_verdict(
    signals: Signals,
    import_mode: ImportMode,
    config: Config | None = None,
    *,
    is_truncated: bool = False,
) -> Verdict:
    """Выдать вердикт ``work_detected`` / ``no_work_detected`` / ``ambiguous``."""
    config = config or Config.defaults()
    mode = import_mode.mode
    min_during = config.n_during_min
    minutes = _minutes_text(signals.threshold_real_ms)

    # ------------------------------------------------------------- вердикт
    no_content = (
        signals.n_real_edits == 0
        and signals.n_filled_table_cells == 0
        and signals.n_drawings == 0
        and signals.n_stickers_with_text == 0
    )
    spread_ok = (
        mode == "full"
        and signals.created_spread_hours is not None
        and signals.created_spread_hours > 24
    )

    if no_content:
        verdict_name = "no_work_detected"
    elif (
        signals.n_real_edits >= min_during
        or (signals.n_filled_table_cells > 0 and signals.n_real_edits > 0)
        or spread_ok
    ):
        verdict_name = "work_detected"
    else:
        verdict_name = "ambiguous"

    # ------------------------------------------------------------- обоснование
    reasons: list[str] = []
    phrase = _edit_phrase(mode)

    cells_mentioned = False
    if verdict_name == "work_detected":
        if signals.n_real_edits > 0:
            reasons.append(
                f"{signals.n_real_edits} "
                f"{plural(signals.n_real_edits, 'объект правился', 'объекта правились', 'объектов правились')} "
                f"{phrase} (updated - created > {minutes} мин)"
            )
        elif signals.n_filled_table_cells > 0:
            reasons.append(
                f"{signals.n_filled_table_cells} "
                f"{plural(signals.n_filled_table_cells, 'ячейка таблицы заполнена', 'ячейки таблиц заполнены', 'ячеек таблиц заполнены')}"
                " и есть правки после создания"
            )
        if spread_ok and signals.created_spread_hours is not None:
            reasons.append(
                "created.t охватывает "
                f"{format_delta_hours(signals.created_spread_hours)} — "
                "это не одномоментная загрузка"
            )
    elif verdict_name == "ambiguous":
        reasons.append(
            f"Правок {phrase}: {signals.n_real_edits} "
            f"(для work_detected нужно >= {min_during})"
        )
        if signals.n_filled_table_cells > 0 and signals.n_real_edits == 0:
            cells_mentioned = True
            reasons.append(
                f"{signals.n_filled_table_cells} "
                f"{plural(signals.n_filled_table_cells, 'ячейка таблицы заполнена', 'ячейки таблиц заполнены', 'ячеек таблиц заполнены')}, "
                "но правок после создания нет"
            )
        if signals.n_objects_updated > 0 and signals.n_real_edits == 0:
            updated = signals.n_objects_updated
            reasons.append(
                f"{updated} "
                f"{plural(updated, 'объект обновлялся', 'объекта обновлялись', 'объектов обновлялись')}, "
                f"но все изменения — в пределах {minutes} мин после создания: "
                "по правилам это не правка, а создание"
            )
    else:
        reasons.append(
            f"Ни один объект не правился после создания/импорта "
            f"(порог {minutes} мин)"
        )
        reasons.append(
            "Заполненных ячеек таблиц: "
            f"{signals.n_filled_table_cells}, рисунков: {signals.n_drawings}, "
            f"стикеров с текстом: {signals.n_stickers_with_text}"
        )
        if signals.n_files > 0:
            reasons.append(
                f"{signals.n_files} "
                f"{plural(signals.n_files, 'файл загружен', 'файла загружено', 'файлов загружено')} — "
                "это вложения, а не работа на доске"
            )

    # --- общие (для любого вердикта) количественные пункты
    if signals.frames_with_edits:
        shown = signals.frames_with_edits[:5]
        labels = [
            f"«{frame.label or 'без названия'}» ({_frame_when(frame)})"
            for frame in shown
        ]
        tail = (
            f", и ещё {len(signals.frames_with_edits) - len(shown)}"
            if len(signals.frames_with_edits) > len(shown)
            else ""
        )
        reasons.append(
            f"{len(signals.frames_with_edits)} "
            f"{plural(len(signals.frames_with_edits), 'фрейм затронут', 'фрейма затронуты', 'фреймов затронуты')}: "
            + ", ".join(labels)
            + tail
        )

    if verdict_name != "no_work_detected":
        if signals.n_filled_table_cells > 0 and not cells_mentioned:
            reasons.append(
                f"{signals.n_filled_table_cells} "
                f"{plural(signals.n_filled_table_cells, 'ячейка таблиц заполнена', 'ячейки таблиц заполнены', 'ячеек таблиц заполнены')}"
            )
        if signals.n_stickers_with_text > 0:
            stickers = signals.n_stickers_with_text
            reasons.append(
                f"{stickers} "
                f"{plural(stickers, 'стикер с текстом', 'стикера с текстом', 'стикеров с текстом')}"
            )
        if signals.n_drawings > 0:
            drawings = signals.n_drawings
            reasons.append(
                f"Рисунков (тип drawing, штрихи пера): {drawings}"
            )
        if signals.n_mind_map_nodes_with_text > 0:
            nodes = signals.n_mind_map_nodes_with_text
            reasons.append(
                f"{nodes} "
                f"{plural(nodes, 'узел mind-map с текстом', 'узла mind-map с текстом', 'узлов mind-map с текстом')}"
            )
        if signals.n_images_photo_like > 0:
            reasons.append(
                f"Изображений (тип image): {signals.n_images}, "
                f"из них {signals.n_images_photo_like} с «фото»-именами "
                "(скрин/фото решения)"
            )
        elif signals.n_images > 0:
            reasons.append(f"Изображений (тип image): {signals.n_images}")
        if signals.n_unique_updated_authors > 0:
            authors = signals.n_unique_updated_authors
            if signals.n_real_edits > 0:
                reasons.append(
                    f"{authors} "
                    f"{plural(authors, 'автор правок', 'автора правок', 'авторов правок')}"
                )
            else:
                reasons.append(
                    f"{authors} "
                    f"{plural(authors, 'автор изменений', 'автора изменений', 'авторов изменений')} "
                    "в updated.a"
                )
        if signals.n_comments > 0:
            reasons.append(f"Комментариев: {signals.n_comments}")
        if signals.created_spread_hours is not None and not spread_ok:
            spread_hours = signals.created_spread_hours
            spread = format_delta_hours(spread_hours)
            if spread_hours < 1:
                tail = "все объекты созданы почти одновременно"
            elif spread_hours <= 24:
                tail = "создание объектов уложилось в несколько часов"
            else:
                tail = "объекты создавались постепенно, но режим не full — опираться на created.t нельзя"
            reasons.append(f"created.t охватывает {spread} — {tail}")
    else:
        # Слабые признаки, которые по правилам не дают work_detected.
        caveats: list[str] = []
        if signals.n_mind_map_nodes_with_text > 0:
            caveats.append(
                f"{signals.n_mind_map_nodes_with_text} узлов mind-map с текстом"
            )
        if signals.n_images_photo_like > 0:
            caveats.append(
                f"{signals.n_images_photo_like} изображений с именами, похожими "
                "на фото/скрин решения"
            )
        for caveat in caveats:
            reasons.append(
                f"Слабый признак (не влияет на вердикт по правилам): {caveat}"
            )

    # ------------------------------------------------------------- warnings
    warnings: list[str] = []
    if mode == "post_import":
        when = (
            format_ms_human(import_mode.top_created_t, config.timezone_name)
            if import_mode.top_created_t is not None
            else "неизвестно когда"
        )
        warnings.append(
            "История до импорта недоступна: все объекты созданы одним пакетом "
            f"({when}). Анализ выполнен только по updated."
        )
    elif mode == "mixed":
        warnings.append(
            "Режим mixed: результат требует ручной проверки — часть объектов "
            "создана вне импорта."
        )
    elif mode == "unknown":
        warnings.append(
            "Режим доски не определён: нет валидных created.t, "
            "анализ опирается только на updated."
        )
    if is_truncated:
        warnings.append(
            "Файл обрезан, часть объектов могла быть потеряна — сигналы "
            "могут быть неполными."
        )
    if signals.n_objects_total == 0:
        warnings.append("В бэкапе нет объектов.")

    # ------------------------------------------------------------- confidence
    strong = _count_strong_signals(signals)
    high_min = config.analysis.classification.confidence_high_min_signals
    low_warn_threshold = config.analysis.classification.warnings_low_threshold

    if verdict_name == "no_work_detected":
        confidence = "high" if mode == "full" and not is_truncated else "medium"
    elif strong == 0:
        confidence = "low"
    elif strong >= high_min and mode in ("full", "post_import") and not is_truncated:
        confidence = "high"
    else:
        confidence = "medium"

    if mode == "mixed" and confidence == "high":
        confidence = "medium"
    if verdict_name == "ambiguous" and confidence == "high":
        # неоднозначный вердикт не может быть «уверенным»
        confidence = "medium"
    if len(warnings) >= low_warn_threshold:
        confidence = "low"

    return Verdict(
        verdict=verdict_name,
        confidence=confidence,
        reasons=reasons,
        warnings=warnings,
        strong_signals=strong,
    )
