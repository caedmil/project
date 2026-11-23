"""Общие конструкторы для тестов (не собираются pytest'ом как тесты)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from board_work_check.models import ParsedObject, ParseResult, Signals


def parsed_object(
    object_id: str,
    object_type: str = "sticker",
    *,
    parent_id: str | None = None,
    created_t: float | None = None,
    updated_t: float | None = None,
    created_a: int | None = 1,
    updated_a: int | None = None,
    text: str | None = None,
    label: str | None = None,
    file_name: str | None = None,
) -> ParsedObject:
    return ParsedObject(
        object_id=object_id,
        type=object_type,
        parent_id=parent_id,
        label=label,
        created_a=created_a,
        created_t=created_t,
        updated_a=updated_a,
        updated_t=updated_t,
        bounds=None,
        text=text,
        file_name=file_name,
    )


def make_parse_result(
    objects: list[ParsedObject],
    *,
    comments_count: int = 0,
    is_truncated: bool = False,
    board_name: str = "Тестовая доска",
    warnings: list[str] | None = None,
) -> ParseResult:
    return ParseResult(
        board_name=board_name,
        objects=objects,
        comments_count=comments_count,
        is_truncated=is_truncated,
        warnings=list(warnings or []),
    )


def make_signals(**overrides: Any) -> Signals:
    base: dict[str, Any] = {
        "n_objects_total": 0,
        "n_objects_by_type": {},
        "n_objects_with_text": 0,
        "n_objects_updated": 0,
        "n_real_edits": 0,
        "n_empty_stickers": 0,
        "n_stickers_with_text": 0,
        "n_filled_table_cells": 0,
        "n_drawings": 0,
        "n_mind_map_nodes_with_text": 0,
        "n_images": 0,
        "n_files": 0,
        "n_comments": 0,
        "n_unique_updated_authors": 0,
        "created_spread_hours": None,
        "frames_with_edits": [],
    }
    base.update(overrides)
    return Signals(**base)


def json_state(paragraphs: list[str]) -> dict[str, str]:
    """``jsonState.children`` в точности как в реальном экспорте: JSON в строке."""
    nodes = [
        {
            "type": "paragraph",
            "key": f"p{index}",
            "children": [{"text": text}],
        }
        for index, text in enumerate(paragraphs)
    ]
    payload = [{"type": "wrapper", "key": "w0", "children": nodes}]
    return {"children": json.dumps(payload, ensure_ascii=False)}


def raw_object(object_id: str = "obj-1", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "type": "sticker",
        "id": object_id,
        "parentId": None,
        "position": {"x": 0, "y": 0},
        "width": 100,
        "height": 100,
        "bounds": {"x": 0, "y": 0, "width": 100, "height": 100, "type": 1},
        "created": {"a": 1, "t": 1_700_000_000_000.0},
        "updated": None,
        "reactions": [],
    }
    base.update(overrides)
    return base


def write_board(
    path: Path,
    objects: list[dict[str, Any]],
    *,
    board_name: str = "Тестовая доска",
    comments: list[dict[str, Any]] | None = None,
) -> Path:
    payload = {
        "boardName": board_name,
        "version": 1,
        "objects": objects,
        "comments": comments if comments is not None else [],
        "backgroundColor": 16119285,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
