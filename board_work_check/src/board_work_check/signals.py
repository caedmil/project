"""Подсчёт сигналов «работы с контентом» по разобранному бэкапу.

Ключевая деталь: содержимое таблиц лежит не на фрейме, а на объекте
``table-cell`` -> ``table`` -> ``frame``. Поэтому фрейм засчитывается как
затронутый, если правка есть у любого его потомка на любой глубине
(включая объекты внутри групп).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from .config import Config
from .models import (
    MS_IN_HOUR,
    FrameWithEdits,
    ImportMode,
    ParsedObject,
    ParseResult,
    Signals,
    ms_to_iso,
)


def _compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(pattern, re.IGNORECASE) for pattern in patterns]


def _nearest_frame_ancestor(
    obj: ParsedObject, by_id: dict[str, ParsedObject]
) -> ParsedObject | None:
    """Ближайший предок-фрейм (через table, group и вложенные фреймы)."""
    seen = {obj.object_id}
    parent_id = obj.parent_id
    while parent_id:
        if parent_id in seen:  # защита от циклов в parentId
            return None
        seen.add(parent_id)
        parent = by_id.get(parent_id)
        if parent is None:
            return None
        if parent.type == "frame":
            return parent
        parent_id = parent.parent_id
    return None


def compute_signals(
    parse_result: ParseResult,
    import_mode: ImportMode,
    config: Config | None = None,
) -> Signals:
    """Посчитать все сигналы по доске."""
    config = config or Config.defaults()
    objects = parse_result.objects
    threshold_ms = config.threshold_real_ms
    by_type: Counter[str] = Counter(obj.type for obj in objects)
    by_id = {obj.object_id: obj for obj in objects}

    real_edits = [obj for obj in objects if obj.is_real_edit(threshold_ms)]

    # --- фреймы и их «настоящие» правки (через всё дерево parentId)
    frames = [obj for obj in objects if obj.type == "frame"]
    edits_by_frame: dict[str, list[ParsedObject]] = defaultdict(list)
    for obj in real_edits:
        frame = _nearest_frame_ancestor(obj, by_id)
        if frame is not None:
            edits_by_frame[frame.object_id].append(obj)

    frames_with_edits: list[FrameWithEdits] = []
    for frame in frames:
        edits = edits_by_frame.get(frame.object_id)
        if not edits:
            continue
        last_edit = max(edits, key=lambda item: (item.updated_t or 0.0, item.object_id))
        last_edit_t = last_edit.updated_t or 0.0
        delta_hours = (
            None
            if frame.created_t is None
            else (last_edit_t - frame.created_t) / MS_IN_HOUR
        )
        frames_with_edits.append(
            FrameWithEdits(
                frame_id=frame.object_id,
                label=frame.label,
                last_edit_iso=ms_to_iso(last_edit_t, config.timezone_name),
                delta_hours=delta_hours,
                edited_object_ids=sorted(
                    (item.object_id for item in edits),
                    key=lambda oid: (
                        by_id[oid].updated_t or 0.0,
                        oid,
                    ),
                ),
                last_edit_t=last_edit_t,
            )
        )
    frames_with_edits.sort(key=lambda item: (item.last_edit_t or 0.0, item.frame_id))

    # --- разброс created.t
    created_ts = [obj.created_t for obj in objects if obj.created_t is not None]
    created_spread_hours: float | None
    if import_mode.mode == "post_import" or not created_ts:
        created_spread_hours = None
    else:
        created_spread_hours = (max(created_ts) - min(created_ts)) / MS_IN_HOUR

    created_counter = Counter(created_ts)
    n_objects_created_same_ms = max(created_counter.values(), default=0)

    # --- слабые сигналы по типам
    n_stickers_total = by_type.get("sticker", 0)
    n_stickers_with_text = sum(
        1 for obj in objects if obj.type == "sticker" and obj.text
    )
    n_empty_stickers = n_stickers_total - n_stickers_with_text
    n_filled_table_cells = sum(
        1 for obj in objects if obj.type == "table-cell" and obj.text
    )
    n_mind_map_nodes_with_text = sum(
        1 for obj in objects if obj.type == "mind-map-node" and obj.text
    )
    n_images = by_type.get("image", 0)

    photo_patterns = _compile_patterns(
        config.analysis.suspicious_image_name_patterns
    )
    n_images_photo_like = sum(
        1
        for obj in objects
        if obj.type == "image"
        and obj.file_name
        and any(pattern.search(obj.file_name) for pattern in photo_patterns)
    )

    n_unique_updated_authors = len(
        {obj.updated_a for obj in objects if obj.updated_a is not None}
    )

    return Signals(
        n_objects_total=len(objects),
        n_objects_by_type=dict(by_type),
        n_objects_with_text=sum(1 for obj in objects if obj.text),
        n_objects_updated=sum(1 for obj in objects if obj.updated_t is not None),
        n_real_edits=len(real_edits),
        n_empty_stickers=n_empty_stickers,
        n_stickers_with_text=n_stickers_with_text,
        n_filled_table_cells=n_filled_table_cells,
        n_drawings=by_type.get("drawing", 0),
        n_mind_map_nodes_with_text=n_mind_map_nodes_with_text,
        n_images=n_images,
        n_files=by_type.get("file", 0),
        n_comments=parse_result.comments_count,
        n_unique_updated_authors=n_unique_updated_authors,
        created_spread_hours=created_spread_hours,
        frames_with_edits=frames_with_edits,
        n_frames_total=len(frames),
        n_frames_without_edits=len(frames) - len(frames_with_edits),
        n_images_photo_like=n_images_photo_like,
        n_stickers_total=n_stickers_total,
        n_objects_created_same_ms=n_objects_created_same_ms,
        threshold_real_ms=threshold_ms,
    )
