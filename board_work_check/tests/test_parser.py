"""Тесты парсера бэкапа: валидный, обрезанный, ZIP и извлечение текста."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from board_work_check.parser import (
    ParseError,
    extract_text,
    parse_backup,
    recover_truncated_json,
    scan_objects_array,
)
from helpers import json_state, raw_object, write_board


def test_valid_json_with_three_objects(workdir: Path) -> None:
    path = write_board(
        workdir / "board.json",
        [raw_object("a"), raw_object("b"), raw_object("c")],
    )

    result = parse_backup(path)

    assert len(result.objects) == 3
    assert [obj.object_id for obj in result.objects] == ["a", "b", "c"]
    assert result.is_truncated is False
    assert result.warnings == []
    assert result.board_name == "Тестовая доска"


def test_truncated_json_recovers_to_last_closed_object(workdir: Path) -> None:
    objects = [raw_object("a"), raw_object("b"), raw_object("c")]
    text = json.dumps(
        {
            "boardName": "Обрезанная доска",
            "version": 1,
            "objects": objects,
            "comments": [],
        },
        ensure_ascii=False,
    )
    cut = text.rindex('"id": "c"')
    path = workdir / "truncated.json"
    path.write_text(text[:cut], encoding="utf-8")

    result = parse_backup(path)

    assert result.is_truncated is True
    assert [obj.object_id for obj in result.objects] == ["a", "b"]
    assert result.board_name == "Обрезанная доска"
    assert any("обрезан" in warning.lower() for warning in result.warnings)


def test_object_without_updated_key_has_none(workdir: Path) -> None:
    without_key = raw_object("a")
    del without_key["updated"]
    explicit_null = raw_object("b", updated=None)
    path = write_board(workdir / "board.json", [without_key, explicit_null])

    result = parse_backup(path)

    assert result.objects[0].updated_t is None
    assert result.objects[0].updated_a is None
    assert result.objects[1].updated_t is None


def test_jsonstate_children_is_string_and_text_extracted(workdir: Path) -> None:
    obj = raw_object("a", jsonState=json_state(["Текст внутри строки"]))
    path = write_board(workdir / "board.json", [obj])

    result = parse_backup(path)

    assert result.objects[0].text == "Текст внутри строки"


def test_multiple_paragraphs_joined_with_space(workdir: Path) -> None:
    obj = raw_object("a", jsonState=json_state(["Первая строка", "Вторая строка"]))
    path = write_board(workdir / "board.json", [obj])

    result = parse_backup(path)

    assert result.objects[0].text == "Первая строка Вторая строка"


def test_empty_text_normalised_to_none(workdir: Path) -> None:
    obj = raw_object("a", jsonState=json_state(["   ", ""]))
    path = write_board(workdir / "board.json", [obj])

    result = parse_backup(path)

    assert result.objects[0].text is None


def test_fractional_updated_t_preserved(workdir: Path) -> None:
    obj = raw_object("a", updated={"a": 42, "t": 1792309592184.5})
    path = write_board(workdir / "board.json", [obj])

    result = parse_backup(path)

    parsed = result.objects[0]
    assert parsed.updated_t == 1792309592184.5
    assert isinstance(parsed.updated_t, float)
    assert parsed.updated_a == 42
    assert isinstance(parsed.created_t, float)


def test_braces_inside_text_do_not_break_recovery(workdir: Path) -> None:
    tricky = "фигурные } и [ и { скобки, а ещё кавычки \" и \\"
    objects = [raw_object("a", jsonState=json_state([tricky]))]
    objects += [raw_object("b"), raw_object("c")]
    text = json.dumps(
        {"boardName": "Скобки", "version": 1, "objects": objects, "comments": []},
        ensure_ascii=False,
    )
    cut = text.rindex('"id": "c"')
    path = workdir / "braces.json"
    path.write_text(text[:cut], encoding="utf-8")

    result = parse_backup(path)

    assert result.is_truncated is True
    assert [obj.object_id for obj in result.objects] == ["a", "b"]
    assert result.objects[0].text == tricky


def test_scan_counts_closed_objects() -> None:
    text = json.dumps({"objects": [{"id": "a"}, {"id": "b"}, {"id": "c"}]})
    scan = scan_objects_array(text)

    assert scan is not None
    assert scan.n_closed_objects == 3
    assert scan.last_object_end is not None


def test_recover_returns_zero_objects_when_first_object_broken() -> None:
    text = '{"boardName": "x", "objects": [{"id": "a", "type": "stick'
    repaired, count = recover_truncated_json(text)
    payload = json.loads(repaired)

    assert count == 0
    assert payload["objects"] == []


def test_zip_export_is_supported(workdir: Path) -> None:
    payload = json.dumps(
        {
            "boardName": "Доска из архива",
            "version": 1,
            "objects": [raw_object("a")],
            "comments": [],
        },
        ensure_ascii=False,
    )
    archive_path = workdir / "board.holst"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("data.json", payload)
        archive.writestr("asset.png", b"\x89PNG")

    result = parse_backup(archive_path)

    assert result.source_kind == "zip"
    assert len(result.objects) == 1
    assert any("ZIP" in warning for warning in result.warnings)


def test_utf8_bom_is_supported(workdir: Path) -> None:
    path = workdir / "bom.json"
    payload = json.dumps(
        {"boardName": "BOM", "version": 1, "objects": [raw_object("a")], "comments": []},
        ensure_ascii=False,
    )
    path.write_text(payload, encoding="utf-8-sig")

    result = parse_backup(path)

    assert result.objects[0].object_id == "a"


def test_garbage_file_raises_parse_error(workdir: Path) -> None:
    path = workdir / "garbage.json"
    path.write_text("это точно не JSON", encoding="utf-8")

    with pytest.raises(ParseError):
        parse_backup(path)


def test_json_without_objects_raises_parse_error(workdir: Path) -> None:
    path = workdir / "empty.json"
    path.write_text('{"boardName": "нет объектов"}', encoding="utf-8")

    with pytest.raises(ParseError):
        parse_backup(path)


def test_comments_are_counted(workdir: Path) -> None:
    path = write_board(
        workdir / "board.json",
        [raw_object("a")],
        comments=[{"id": "c1", "text": "привет"}, {"id": "c2", "text": "ещё"}],
    )

    result = parse_backup(path)

    assert result.comments_count == 2


def test_duplicate_and_broken_ids_are_skipped(workdir: Path) -> None:
    path = write_board(
        workdir / "board.json",
        [raw_object("a"), raw_object("a"), {"type": "sticker"}, raw_object("b")],
    )

    result = parse_backup(path)

    assert [obj.object_id for obj in result.objects] == ["a", "b"]
    assert any("повторяющимся id" in warning for warning in result.warnings)
    assert any("без корректного id" in warning for warning in result.warnings)


def test_broken_jsonstate_gives_none_text(workdir: Path) -> None:
    obj = raw_object("a", jsonState={"children": "{не json"})
    path = write_board(workdir / "board.json", [obj])

    result = parse_backup(path)

    assert result.objects[0].text is None


def test_arrow_texts_fallback(workdir: Path) -> None:
    obj = raw_object(
        "a", type="arrow", texts=[{"id": "t1", "text": "подпись стрелки"}]
    )
    path = write_board(workdir / "board.json", [obj])

    result = parse_backup(path)

    assert result.objects[0].text == "подпись стрелки"


def test_extract_text_accepts_already_parsed_children() -> None:
    raw = {"jsonState": {"children": [{"type": "paragraph", "text": "готовый список"}]}}

    assert extract_text(raw) == "готовый список"
