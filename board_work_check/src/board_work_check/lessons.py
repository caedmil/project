"""Анализ признаков разбора результатов студентов по расписанию занятий.

Бэкап содержит только последнее состояние и updated.t, а не историю версий.
Поэтому модуль сообщает о признаках, а не доказывает смысл изменения.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta

from .config import Config
from .models import MS_IN_HOUR, ParsedObject, ParseResult, resolve_timezone

SCHEDULE: tuple[tuple[int, time, time], ...] = (
    (1, time(8, 30), time(10, 0)),
    (2, time(10, 15), time(11, 45)),
    (3, time(12, 5), time(13, 35)),
    (4, time(14, 5), time(15, 35)),
    (5, time(15, 55), time(17, 25)),
    (6, time(17, 40), time(19, 10)),
    (7, time(19, 20), time(20, 50)),
)
FOLLOW_UP = timedelta(hours=24)


@dataclass(slots=True)
class TeacherGuess:
    author_id: int | None
    status: str
    confidence: str
    edited_existing_counts: dict[int, int]


@dataclass(slots=True)
class LessonFinding:
    frame_id: str
    frame_label: str
    lesson_label_number: str | None
    local_date: str
    slot_number: int
    start_iso: str
    end_iso: str
    status: str
    confidence: str
    preparation_iso: str | None
    image_uploads: int
    first_image_iso: str | None
    reviewed_material_iso: str | None
    reviewed_object_ids: list[str]
    teacher_author_id: int | None
    explanation: str
    limitations: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _local(ms: float, timezone: str) -> datetime:
    tz, _ = resolve_timezone(timezone)
    return datetime.fromtimestamp(ms / 1000.0, tz=tz)


def _descendants(frame: ParsedObject, by_id: dict[str, ParsedObject]) -> list[ParsedObject]:
    children: dict[str, list[ParsedObject]] = defaultdict(list)
    for obj in by_id.values():
        if obj.parent_id:
            children[obj.parent_id].append(obj)
    result: list[ParsedObject] = []
    stack = list(children.get(frame.object_id, []))
    seen = {frame.object_id}
    while stack:
        obj = stack.pop()
        if obj.object_id in seen:
            continue
        seen.add(obj.object_id)
        result.append(obj)
        stack.extend(children.get(obj.object_id, []))
    return result


def infer_teacher_author(
    objects: list[ParsedObject], threshold_ms: int, override_author_id: int | None = None
) -> TeacherGuess:
    """Оценить автора по работе со старым контентом и неимпортным добавлениям.

    Фото, созданные в учебные окна, не повышают рейтинг автора: это могут быть
    загрузки студентов. Timestamp-кластеры массового импорта также исключаются.
    """
    if override_author_id is not None:
        return TeacherGuess(override_author_id, "configured", "medium", {})

    ts_counts = Counter(obj.created_t for obj in objects if obj.created_t is not None)
    bulk_threshold = max(5, int(len(objects) * 0.1))
    bulk_times = {stamp for stamp, count in ts_counts.items() if count >= bulk_threshold}
    counts: Counter[int] = Counter()
    for obj in objects:
        delta = obj.edit_delta_ms()
        if obj.updated_a is not None and delta is not None and delta > threshold_ms:
            counts[obj.updated_a] += 1
        if obj.created_a is None or obj.created_t is None or obj.created_t in bulk_times:
            continue
        if obj.type == "image" or obj.type == "frame":
            continue
        counts[obj.created_a] += 1

    ordered = counts.most_common()
    if not ordered:
        return TeacherGuess(None, "unknown", "low", {})
    leader, count = ordered[0]
    runner_up = ordered[1][1] if len(ordered) > 1 else 0
    clear_leader = count >= 3 and (runner_up == 0 or count >= 2 * runner_up)
    status = "inferred" if clear_leader else "ambiguous"
    confidence = "medium" if clear_leader else "low"
    return TeacherGuess(leader if clear_leader else None, status, confidence, dict(counts))


def _lesson_slot(moment: datetime) -> tuple[int, datetime, datetime] | None:
    local_time = moment.timetz().replace(tzinfo=None)
    for number, begins, ends in SCHEDULE:
        if begins <= local_time < ends:
            return (
                number,
                datetime.combine(moment.date(), begins, tzinfo=moment.tzinfo),
                datetime.combine(moment.date(), ends, tzinfo=moment.tzinfo),
            )
    return None


def _utc_ms(moment: datetime) -> float:
    return moment.timestamp() * 1000.0


def _frame_quality(items: list[ParsedObject]) -> bool:
    """True when the frame subtree's creation timestamps are import-like."""
    created = [obj.created_t for obj in items if obj.created_t is not None]
    if len(created) < 3:
        return False
    counts = Counter(created)
    top_share = counts.most_common(1)[0][1] / len(created)
    spread_hours = (max(created) - min(created)) / MS_IN_HOUR
    return (
        top_share >= 0.9
        or (counts.most_common(1)[0][1] >= 5 and top_share >= 0.75)
        or (len(counts) <= 3 and spread_hours < 0.1)
    )


def _label_numbers(label: str | None) -> list[int]:
    """Extract lesson sequence numbers from a frame label."""
    if not label:
        return []
    match = re.search(
        r"(?:пара|практика|praktika|practice)\s*(\d+)\s*[-–—-]\s*(\d+)",
        label,
        re.IGNORECASE,
    )
    if match:
        first, last = int(match.group(1)), int(match.group(2))
        return list(range(min(first, last), max(first, last) + 1))
    match = re.search(r"(?:пара|практика|praktika|practice)\s*(\d+)", label, re.IGNORECASE)
    return [int(match.group(1))] if match else []


def _compress_ranges(numbers: set[int]) -> list[str]:
    """Render sorted lesson numbers as compact contiguous ranges."""
    if not numbers:
        return []
    ordered = sorted(numbers)
    ranges: list[tuple[int, int]] = []
    start = previous = ordered[0]
    for number in ordered[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append((start, previous))
        start = previous = number
    ranges.append((start, previous))
    return [str(first) if first == last else f"{first}-{last}" for first, last in ranges]


def analyze_lessons(
    parsed: ParseResult,
    config: Config | None = None,
    *,
    follow_up_hours: int = 24,
) -> tuple[TeacherGuess, list[LessonFinding]]:
    """Сгруппировать события фреймов по реальным временным слотам.

    Кандидат занятия появляется только если внутри фрейма есть image,
    созданный в одном из расписанных интервалов. Старый материал считается
    затронутым при updated.t после первой фотографии и не позднее 24 ч после
    конца слота, если updated.a совпадает с уверенно выведенным автором-преподавателем.
    """
    config = config or Config.defaults()
    timezone = config.timezone_name
    threshold = config.threshold_real_ms
    if follow_up_hours < 0:
        raise ValueError("follow_up_hours must be non-negative")
    follow_up = timedelta(hours=follow_up_hours)
    teacher = infer_teacher_author(
        parsed.objects, threshold, config.analysis.teacher_author_id
    )
    by_id = {obj.object_id: obj for obj in parsed.objects}
    findings: list[LessonFinding] = []
    frames_by_id = {obj.object_id: obj for obj in parsed.objects if obj.type == "frame"}
    imported_lesson_numbers: set[int] = set()
    import_candidates: list[tuple[ParsedObject, list[ParsedObject]]] = []
    for candidate_frame in frames_by_id.values():
        candidate_items = _descendants(candidate_frame, by_id)
        if _frame_quality(candidate_items):
            imported_lesson_numbers.update(_label_numbers(candidate_frame.label))
        import_candidates.append((candidate_frame, candidate_items))

    for frame in (obj for obj in parsed.objects if obj.type == "frame"): 
        items = _descendants(frame, by_id)
        images_by_slot: dict[tuple[date, int], list[ParsedObject]] = defaultdict(list)
        for obj in items:
            if obj.type != "image" or obj.created_t is None:
                continue
            moment = _local(obj.created_t, timezone)
            slot = _lesson_slot(moment)
            if slot is not None:
                images_by_slot[(moment.date(), slot[0])].append(obj)

        for (lesson_date, slot_number), images in sorted(images_by_slot.items()):
            _, starts_at, ends_at = next(s for s in SCHEDULE if s[0] == slot_number)
            tzinfo = _local(images[0].created_t or 0.0, timezone).tzinfo
            start = datetime.combine(lesson_date, starts_at, tzinfo=tzinfo)
            end = datetime.combine(lesson_date, ends_at, tzinfo=tzinfo)
            start_ms, end_ms = _utc_ms(start), _utc_ms(end)
            follow_end_ms = _utc_ms(end + follow_up)

            frame_imported = _frame_quality(items)
            frame_lesson_numbers = _label_numbers(frame.label)
            if frame_lesson_numbers and all(
                number in imported_lesson_numbers for number in frame_lesson_numbers
            ):
                frame_imported = True
            if frame_lesson_numbers and not _frame_quality(items):
                frame_imported = False
            prep = [
                obj.created_t
                for obj in items
                if obj.created_t is not None
                and start_ms - 24 * MS_IN_HOUR <= obj.created_t < start_ms
                and not frame_imported
            ]
            preparation_iso = (
                _local(min(prep), timezone).isoformat(timespec="minutes") if prep else None
            )

            first_image_ms = min(obj.created_t for obj in images if obj.created_t is not None)
            old_material_updates = [
                obj
                for obj in items
                if obj.created_t is not None
                and obj.updated_t is not None
                and obj.created_t < start_ms
                and obj.updated_t >= first_image_ms
                and obj.updated_t <= follow_end_ms
                and obj.updated_a is not None
                and teacher.author_id is not None
                and obj.updated_a == teacher.author_id
                and obj.updated_t > obj.created_t
            ]
            old_material_updates.sort(key=lambda obj: (obj.updated_t or 0.0, obj.object_id))
            has_update = bool(old_material_updates)
            limitations = [
                "Бэкап хранит только последнее updated.t: не видна старая версия и нельзя доказать содержание изменения."
            ]
            if teacher.author_id is None:
                limitations.append("Не удалось уверенно определить автора-преподавателя по правкам старых объектов.")
            if frame_imported:
                limitations.append("created.t фрейма выглядит пакетным импортом; подготовка до пары может быть не восстановлена.")

            if frame_imported:
                status = "not_analyzable_imported"
                confidence = "low"
                explanation = "Временные метки фрейма похожи на пакетный импорт; нельзя надёжно привязать объекты к занятию."
                review_iso = None
                review_ids = []
            elif has_update:
                status = "review_signs_found"
                confidence = "medium" if teacher.status in {"inferred", "configured"} else "low"
                explanation = "После загрузки изображений обновлён ранее созданный материал предполагаемого преподавателя."
                review_iso = _local(old_material_updates[-1].updated_t or 0.0, timezone).isoformat(timespec="minutes")
                review_ids = [obj.object_id for obj in old_material_updates]
            else:
                status = "no_review_signs_found"
                confidence = "medium" if teacher.status in {"inferred", "configured"} else "low"
                explanation = "Изображения появились во время занятия, но обновление ранее созданного материала преподавателем не найдено."
                review_iso = None
                review_ids = []
            if parsed.is_truncated:
                confidence = "low"
                limitations.append("Бэкап обрезан; часть событий могла отсутствовать.")

            label_range = re.search(r"(?:пара|практика|praktika|practice)\s*(\d+)\s*[-–—-]\s*(\d+)", frame.label or "", re.IGNORECASE)
            label_single = re.search(r"(?:пара|практика|praktika|practice)\s*(\d+)", frame.label or "", re.IGNORECASE)
            label_number = (
                f"{label_range.group(1)}-{label_range.group(2)}"
                if label_range
                else label_single.group(1) if label_single else None
            )
            findings.append(
                LessonFinding(
                    frame_id=frame.object_id,
                    frame_label=frame.label or "Без названия",
                    lesson_label_number=label_number,
                    local_date=lesson_date.isoformat(),
                    slot_number=slot_number,
                    start_iso=start.isoformat(timespec="minutes"),
                    end_iso=end.isoformat(timespec="minutes"),
                    status=status,
                    confidence=confidence,
                    preparation_iso=preparation_iso,
                    image_uploads=len(images),
                    first_image_iso=_local(first_image_ms, timezone).isoformat(timespec="minutes"),
                    reviewed_material_iso=review_iso,
                    reviewed_object_ids=review_ids,
                    teacher_author_id=teacher.author_id,
                    explanation=explanation,
                    limitations=limitations,
                )
            )
    findings.sort(
        key=lambda f: (
            min(_label_numbers(f.frame_label), default=10**9),
            f.local_date,
            f.slot_number,
            f.frame_label.casefold(),
        )
    )
    return teacher, findings


def lesson_report_payload(
    parsed: ParseResult, teacher: TeacherGuess, findings: list[LessonFinding]
) -> dict[str, object]:
    return {
        "board": parsed.board_name,
        "meta": {
            "source_path": parsed.source_path,
            "source_kind": parsed.source_kind,
            "objects_total": len(parsed.objects),
            "is_truncated": parsed.is_truncated,
        },
        "teacher_author": asdict(teacher),
        "lessons": [item.to_dict() for item in findings],
        "limitations": [
            "Выводы основаны на одном snapshot; факт изменения содержания без истории версий/diff не доказать.",
            "Загрузка изображения во время пары — признак появления материала, не доказательство авторства студента.",
        ],
    }


def render_lesson_text(parsed: ParseResult, teacher: TeacherGuess, findings: list[LessonFinding]) -> str:
    lines = [f"Доска: {parsed.board_name or 'без названия'}", "Анализ пар:"]
    if teacher.author_id is not None:
        marker = " (задан в конфигурации)" if teacher.status == "configured" else " (предположение по активности)"
        lines.append(f"Предполагаемый преподаватель: автор {teacher.author_id}{marker}")
    else:
        lines.append("Предполагаемый преподаватель: определить надёжно не удалось")
    by_id = {obj.object_id: obj for obj in parsed.objects}
    imported_numbers: set[int] = set()
    nonimported_frame_numbers: set[int] = set()
    for frame in (obj for obj in parsed.objects if obj.type == "frame"):
        frame_numbers = set(_label_numbers(frame.label))
        if not frame_numbers:
            continue
        if _frame_quality(_descendants(frame, by_id)):
            imported_numbers.update(frame_numbers)
        else:
            nonimported_frame_numbers.update(frame_numbers)
    for item in findings:
        if item.status == "not_analyzable_imported":
            imported_numbers.update(_label_numbers(item.frame_label))

    imported_numbers.difference_update(nonimported_frame_numbers)
    number_status: dict[int, str] = {}
    for item in findings:
        if item.status == "not_analyzable_imported":
            continue
        outcome = "worked" if item.status == "review_signs_found" else "not_found"
        for number in _label_numbers(item.frame_label):
            if number not in imported_numbers:
                previous = number_status.get(number)
                number_status[number] = (
                    "worked" if outcome == "worked" or previous == "worked" else "not_found"
                )

    classifications: dict[int, str] = {
        number: "imported" for number in imported_numbers
    }
    classifications.update(number_status)
    # If one non-imported frame reports a lesson range, expand it to individual
    # lesson numbers so surrounding imported lessons are not swallowed by that range.
    for item in findings:
        if item.status == "not_analyzable_imported":
            continue
        label_range = re.search(
            r"(?:пара|практика|praktika|practice)\s*(\d+)\s*[-–—-]\s*(\d+)",
            item.frame_label,
            re.IGNORECASE,
        )
        if label_range:
            first_number, last_number = map(int, label_range.groups())
            for number in range(min(first_number, last_number), max(first_number, last_number) + 1):
                if number not in classifications:
                    classifications[number] = "not_found"
    if not classifications:
        lines.extend([
            "Занятия с изображениями, добавленными внутри заданных временных интервалов, не найдены.",
            "Это не означает, что преподаватель не работал: snapshot не содержит полной истории событий.",
        ])
        if parsed.is_truncated:
            lines.append("Предупреждение: бэкап обрезан, часть событий могла отсутствовать.")
        return "\n".join(lines)

    ordered_numbers = sorted(classifications)
    groups: list[tuple[int, int, str]] = []
    group_start = previous_number = ordered_numbers[0]
    group_kind = classifications[group_start]
    for number in ordered_numbers[1:]:
        kind = classifications[number]
        if number == previous_number + 1 and kind == group_kind:
            previous_number = number
            continue
        groups.append((group_start, previous_number, group_kind))
        group_start = previous_number = number
        group_kind = kind
    groups.append((group_start, previous_number, group_kind))
    for first, last, kind in groups:
        label = str(first) if first == last else f"{first}-{last}"
        if kind == "imported":
            lines.append(f"Пары {label} импортированы, их не анализируем.")
        else:
            lines.append(f"Пара {label} не импортирована.")
            lines.append(
                "Работа велась: обнаружены признаки разбора результатов."
                if kind == "worked"
                else "Работа не велась: признаки разбора результатов не обнаружены."
            )

    if parsed.is_truncated:
        lines.append("Предупреждение: бэкап обрезан, часть событий могла отсутствовать.")
    lines.append("Примечание: отсутствие признаков не доказывает отсутствие работы; snapshot не содержит истории версий.")
    return "\n".join(lines)
