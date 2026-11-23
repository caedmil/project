"""Разбор JSON-бэкапа доски.

Модуль решает три практические задачи:

1. Достать JSON из файла: поддерживается и «голый» JSON, и ZIP-архив
   экспорта (``.holst``/``.rtb``), внутри которого лежит ``data.json``.
2. Пережить обрезанный файл: если ``json.loads`` упал, восстанавливаем
   массив ``objects`` до последнего полностью закрытого объекта.
3. Привести объекты к плоскому :class:`ParsedObject`, включая текст из
   ``jsonState.children`` (это JSON **внутри строки**).
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ParsedObject, ParseResult

JSON_MEMBER_CANDIDATES = ("data.json",)
SUPPORTED_ENCODINGS = ("utf-8-sig", "cp1251", "latin-1")


class ParseError(RuntimeError):
    """Файл не удалось разобрать даже после восстановления."""


# --------------------------------------------------------------------------- I/O


def _read_payload(path: Path) -> tuple[bytes, list[str], str]:
    """Вернуть (байты, warnings, вид источника)."""
    warnings: list[str] = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            member = next(
                (name for name in names if name in JSON_MEMBER_CANDIDATES), None
            )
            if member is None:
                member = next(
                    (
                        name
                        for name in names
                        if name.lower().endswith("data.json")
                    ),
                    None,
                )
            if member is None:
                raise ParseError(
                    "входной файл — ZIP-архив, но внутри нет data.json"
                )
            try:
                payload = archive.read(member)
            except (KeyError, zipfile.BadZipFile, RuntimeError) as exc:
                raise ParseError(f"не удалось прочитать {member} из архива: {exc}") from exc
        warnings.append(
            f"Входной файл — ZIP-архив экспорта, JSON прочитан из «{member}»."
        )
        return payload, warnings, "zip"
    try:
        return path.read_bytes(), warnings, "json"
    except OSError as exc:
        raise ParseError(f"не удалось прочитать файл: {exc}") from exc


def _decode(raw: bytes) -> tuple[str, list[str]]:
    for encoding in SUPPORTED_ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        warnings: list[str] = []
        if encoding != SUPPORTED_ENCODINGS[0]:
            warnings.append(
                f"Файл прочитан в кодировке {encoding}, а не UTF-8."
            )
        return text, warnings
    raise ParseError("не удалось определить кодировку файла")


# ------------------------------------------------------------- восстановление


@dataclass(slots=True)
class _ObjectsScan:
    array_start: int
    last_object_end: int | None
    n_closed_objects: int


def _is_key_of_root(text: str, quote_index: int) -> bool:
    """После закрывающей кавычки идёт ``:`` (значит, это ключ, а не значение)."""
    index = quote_index + 1
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index < len(text) and text[index] == ":"


def scan_objects_array(text: str) -> _ObjectsScan | None:
    """Найти массив ``objects`` и границы последнего закрытого объекта.

    Ручной проход посимвольно со счётчиком глубины ``{}``/``[]`` и
    корректной обработкой строковых литералов (кавычки и экранирование),
    поэтому скобки внутри текстов стикеров не ломают разметку.
    """
    depth = 0
    in_string = False
    escaped = False
    string_start = -1
    expect_array = False
    array_start = -1
    objects_depth = -1
    in_objects = False
    last_object_end: int | None = None
    n_closed = 0

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
                if (
                    depth == 1
                    and not in_objects
                    and not expect_array
                    and text[string_start + 1 : index] == "objects"
                    and _is_key_of_root(text, index)
                ):
                    expect_array = True
            continue

        if char == '"':
            in_string = True
            string_start = index
            continue

        if char in "{[":
            depth += 1
            if char == "[" and expect_array:
                expect_array = False
                in_objects = True
                array_start = index
                objects_depth = depth
            continue

        if char in "}]":
            depth -= 1
            if in_objects:
                if char == "}" and depth == objects_depth:
                    last_object_end = index + 1
                    n_closed += 1
                elif char == "]" and depth == objects_depth - 1:
                    in_objects = False
            continue

    if array_start < 0:
        return None
    return _ObjectsScan(
        array_start=array_start,
        last_object_end=last_object_end,
        n_closed_objects=n_closed,
    )


def recover_truncated_json(text: str) -> tuple[str, int]:
    """Обрезать текст до последнего валидного объекта и закрыть скобки.

    Возвращает ``(исправленный_текст, сколько_объектов_уцелело)``.
    """
    scan = scan_objects_array(text)
    if scan is None:
        raise ParseError(
            "не удалось найти массив objects: файл повреждён или это не бэкап доски"
        )
    if scan.last_object_end is None:
        repaired = text[: scan.array_start + 1] + "]}"
        return repaired, 0
    repaired = text[: scan.last_object_end] + "]}"
    return repaired, scan.n_closed_objects


def load_document(text: str) -> tuple[dict[str, Any], bool, list[str]]:
    """Распарсить JSON, при необходимости восстановив обрезанный файл."""
    try:
        document = json.loads(text)
        return _ensure_mapping(document), False, []
    except json.JSONDecodeError as exc:
        original_error = exc

    repaired, n_objects = recover_truncated_json(text)
    try:
        document = json.loads(repaired)
    except json.JSONDecodeError as exc:
        raise ParseError(
            f"JSON повреждён и не восстанавливается: {exc} "
            f"(исходная ошибка: {original_error})"
        ) from exc

    warnings = [
        "Файл обрезан: JSON восстановлен до последнего закрытого объекта "
        f"в массиве objects — прочитано объектов: {n_objects}; "
        "остаток файла отброшен, часть объектов могла быть потеряна."
    ]
    return _ensure_mapping(document), True, warnings


def _ensure_mapping(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ParseError(
            f"корень JSON должен быть объектом, получено: {type(document).__name__}"
        )
    return document


# ------------------------------------------------------------------- извлечение


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def _parse_stamp(container: Any) -> tuple[int | None, float | None]:
    """Разобрать ``{"a": author, "t": ms}``; ``None``/мусор -> (None, None)."""
    if not isinstance(container, dict):
        return None, None
    return _to_int(container.get("a")), _to_float(container.get("t"))


def _collect_text(node: Any, parts: list[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _collect_text(item, parts)
        return
    if not isinstance(node, dict):
        return
    text = node.get("text")
    if isinstance(text, str) and text.strip():
        parts.append(text)
    children = node.get("children")
    if isinstance(children, (list, dict)):
        _collect_text(children, parts)


def _collect_from_texts(value: Any, parts: list[str]) -> None:
    """Резервный источник: поле ``texts`` у стрелок в реальном экспорте."""
    if not isinstance(value, list):
        return
    for item in value:
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
        elif isinstance(item, str) and item.strip():
            parts.append(item)


def normalize_text(value: str) -> str | None:
    """Trim + collapse whitespace; пустая строка -> ``None``."""
    normalized = " ".join(value.split())
    return normalized or None


def extract_text(raw: dict[str, Any]) -> str | None:
    """Склеить текст из ``jsonState.children`` (JSON внутри строки)."""
    parts: list[str] = []
    state = raw.get("jsonState")
    if isinstance(state, dict):
        children = state.get("children")
        if isinstance(children, str):
            try:
                children = json.loads(children)
            except (json.JSONDecodeError, TypeError):
                children = None
        if isinstance(children, (list, dict)):
            _collect_text(children, parts)
    if not parts:
        _collect_from_texts(raw.get("texts"), parts)
    if not parts:
        return None
    return normalize_text(" ".join(parts))


def _pick_file_name(raw: dict[str, Any]) -> str | None:
    for key in ("originalName", "displayFileName", "name", "fileName"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _clean_label(value: Any) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None


def build_object(raw: dict[str, Any]) -> ParsedObject:
    """Преобразовать сырой объект бэкапа в :class:`ParsedObject`."""
    object_type = raw.get("type")
    if not isinstance(object_type, str) or not object_type:
        object_type = "unknown"

    parent = raw.get("parentId")
    parent_id = parent if isinstance(parent, str) and parent else None

    created_a, created_t = _parse_stamp(raw.get("created"))
    updated_a, updated_t = _parse_stamp(raw.get("updated"))

    bounds = raw.get("bounds")
    return ParsedObject(
        object_id=raw["id"],
        type=object_type,
        parent_id=parent_id,
        label=_clean_label(raw.get("labelText")),
        created_a=created_a,
        created_t=created_t,
        updated_a=updated_a,
        updated_t=updated_t,
        bounds=bounds if isinstance(bounds, dict) else None,
        text=extract_text(raw),
        file_name=_pick_file_name(raw),
    )


def _iter_raw_objects(document: dict[str, Any]) -> Iterable[dict[str, Any]]:
    objects = document.get("objects")
    if objects is None:
        raise ParseError("в JSON нет массива objects — это не бэкап доски")
    if not isinstance(objects, list):
        raise ParseError(
            f"поле objects должно быть массивом, получено: {type(objects).__name__}"
        )
    for item in objects:
        if isinstance(item, dict):
            yield item


def parse_backup(path: str | Path) -> ParseResult:
    """Полный разбор бэкапа по пути к файлу."""
    path = Path(path)
    raw_bytes, warnings, source_kind = _read_payload(path)
    text, decode_warnings = _decode(raw_bytes)
    warnings.extend(decode_warnings)

    document, is_truncated, load_warnings = load_document(text)
    warnings.extend(load_warnings)

    version = document.get("version")
    if version is not None and version != 1:
        warnings.append(
            f"Неизвестная версия формата доски: version={version!r}, "
            "разбор выполнен по правилам версии 1."
        )

    raw_objects = list(_iter_raw_objects(document))
    objects: list[ParsedObject] = []
    skipped = 0
    seen_ids: set[str] = set()
    duplicates = 0
    for raw in raw_objects:
        object_id = raw.get("id")
        if not isinstance(object_id, str) or not object_id:
            skipped += 1
            continue
        if object_id in seen_ids:
            duplicates += 1
            continue
        seen_ids.add(object_id)
        objects.append(build_object(raw))

    if skipped:
        warnings.append(
            f"Пропущено объектов без корректного id: {skipped}."
        )
    if duplicates:
        warnings.append(
            f"Пропущено объектов с повторяющимся id: {duplicates}."
        )

    comments = document.get("comments")
    if comments is None:
        comments_count = 0
    elif isinstance(comments, list):
        comments_count = len(comments)
    else:
        comments_count = 0
        warnings.append(
            f"Поле comments имеет неожиданный тип {type(comments).__name__}, "
            "комментарии не учтены."
        )

    board_name = document.get("boardName")
    if not isinstance(board_name, str):
        board_name = ""

    return ParseResult(
        board_name=board_name.strip(),
        objects=objects,
        comments_count=comments_count,
        is_truncated=is_truncated,
        warnings=warnings,
        source_path=str(path),
        source_kind=source_kind,
        size_bytes=len(raw_bytes),
    )
