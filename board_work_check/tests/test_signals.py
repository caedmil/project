"""Тесты подсчёта сигналов: правки, вложенность таблиц, слабые признаки."""

from __future__ import annotations

from board_work_check.config import Config
from board_work_check.import_detector import detect_import_mode
from board_work_check.models import ParseResult
from board_work_check.signals import compute_signals
from helpers import make_parse_result, parsed_object

HOUR_MS = 3_600_000.0
BASE_MS = 1_700_000_000_000.0


def _signals(
    objects: list,
    *,
    config: Config | None = None,
    comments: int = 0,
    truncated: bool = False,
) -> tuple:
    config = config or Config.defaults()
    parse_result = make_parse_result(
        objects, comments_count=comments, is_truncated=truncated
    )
    import_mode = detect_import_mode(objects, config)
    return compute_signals(parse_result, import_mode, config), import_mode


def test_three_real_edits_are_counted() -> None:
    objects = [
        parsed_object(f"o{index}", created_t=BASE_MS, updated_t=BASE_MS + 400_000)
        for index in range(3)
    ]

    signals, _ = _signals(objects)

    assert signals.n_real_edits == 3
    assert signals.n_objects_updated == 3


def test_threshold_is_strictly_greater() -> None:
    objects = [
        parsed_object("exact", created_t=BASE_MS, updated_t=BASE_MS + 300_000),
        parsed_object("over", created_t=BASE_MS + 1, updated_t=BASE_MS + 1 + 300_001),
    ]

    signals, _ = _signals(objects)

    assert signals.n_real_edits == 1
    assert signals.n_objects_updated == 2


def test_filled_table_cells_are_counted() -> None:
    objects = [
        parsed_object("frame", "frame", created_t=BASE_MS),
        parsed_object("table", "table", parent_id="frame", created_t=BASE_MS + 1),
        parsed_object("c1", "table-cell", parent_id="table", created_t=BASE_MS + 2, text="A"),
        parsed_object("c2", "table-cell", parent_id="table", created_t=BASE_MS + 3, text="B"),
        parsed_object("c3", "table-cell", parent_id="table", created_t=BASE_MS + 4, text="C"),
        parsed_object("c4", "table-cell", parent_id="table", created_t=BASE_MS + 5),
    ]

    signals, _ = _signals(objects)

    assert signals.n_filled_table_cells == 3
    assert signals.n_objects_by_type["table-cell"] == 4


def test_table_cell_edit_marks_frame_as_touched() -> None:
    objects = [
        parsed_object("frame", "frame", created_t=BASE_MS, label="Практика"),
        parsed_object("table", "table", parent_id="frame", created_t=BASE_MS + 1),
        parsed_object(
            "cell",
            "table-cell",
            parent_id="table",
            created_t=BASE_MS + 2,
            updated_t=BASE_MS + 3 * HOUR_MS,
            updated_a=7,
            text="заполнено",
        ),
    ]

    signals, _ = _signals(objects)

    assert signals.n_real_edits == 1
    assert len(signals.frames_with_edits) == 1
    frame = signals.frames_with_edits[0]
    assert frame.frame_id == "frame"
    assert frame.label == "Практика"
    assert frame.edited_object_ids == ["cell"]
    assert frame.delta_hours == 3.0
    assert frame.last_edit_iso is not None


def test_group_between_frame_and_object_still_attributes() -> None:
    objects = [
        parsed_object("frame", "frame", created_t=BASE_MS),
        parsed_object("group", "group", parent_id="frame", created_t=BASE_MS + 1),
        parsed_object(
            "sticker",
            "sticker",
            parent_id="group",
            created_t=BASE_MS + 2,
            updated_t=BASE_MS + HOUR_MS,
            text="текст",
        ),
    ]

    signals, _ = _signals(objects)

    assert [frame.frame_id for frame in signals.frames_with_edits] == ["frame"]


def test_frames_without_edits_are_counted() -> None:
    objects = [
        parsed_object("f1", "frame", created_t=BASE_MS),
        parsed_object("f2", "frame", created_t=BASE_MS + 1),
        parsed_object(
            "s1",
            "sticker",
            parent_id="f1",
            created_t=BASE_MS + 2,
            updated_t=BASE_MS + HOUR_MS,
            text="x",
        ),
    ]

    signals, _ = _signals(objects)

    assert signals.n_frames_total == 2
    assert signals.n_frames_without_edits == 1


def test_post_import_created_spread_is_none() -> None:
    objects = [
        parsed_object(f"o{index}", created_t=BASE_MS) for index in range(5)
    ]

    signals, import_mode = _signals(objects)

    assert import_mode.mode == "post_import"
    assert signals.created_spread_hours is None
    assert signals.n_objects_created_same_ms == 5


def test_full_mode_created_spread_in_hours() -> None:
    objects = [
        parsed_object(f"o{index}", created_t=BASE_MS + index * HOUR_MS)
        for index in range(10)
    ]

    signals, import_mode = _signals(objects)

    assert import_mode.mode == "full"
    assert signals.created_spread_hours == 9.0


def test_stickers_with_text_and_empty() -> None:
    objects = [
        parsed_object("s1", "sticker", created_t=BASE_MS, text="есть текст"),
        parsed_object("s2", "sticker", created_t=BASE_MS + 1),
        parsed_object("s3", "sticker", created_t=BASE_MS + 2, text=""),
    ]

    signals, _ = _signals(objects)

    assert signals.n_stickers_total == 3
    assert signals.n_stickers_with_text == 1
    assert signals.n_empty_stickers == 2


def test_photo_like_image_names() -> None:
    objects = [
        parsed_object("i1", "image", created_t=BASE_MS, file_name="IMG_2024.jpg"),
        parsed_object("i2", "image", created_t=BASE_MS + 1, file_name="diagram.png"),
        parsed_object("i3", "image", created_t=BASE_MS + 2, file_name="скрин решения.png"),
        parsed_object("i4", "image", created_t=BASE_MS + 3, file_name="a1b2c3.png"),
    ]

    signals, _ = _signals(objects)

    assert signals.n_images == 4
    assert signals.n_images_photo_like == 2


def test_unique_updated_authors_ignore_none() -> None:
    objects = [
        parsed_object("a", created_t=BASE_MS, updated_t=BASE_MS + 1, updated_a=1),
        parsed_object("b", created_t=BASE_MS, updated_t=BASE_MS + 2, updated_a=2),
        parsed_object("c", created_t=BASE_MS, updated_t=BASE_MS + 3, updated_a=None),
        parsed_object("d", created_t=BASE_MS, updated_t=BASE_MS + 4, updated_a=1),
    ]

    signals, _ = _signals(objects)

    assert signals.n_objects_updated == 4
    assert signals.n_unique_updated_authors == 2


def test_drawings_and_mind_map_nodes() -> None:
    objects = [
        parsed_object("d1", "drawing", created_t=BASE_MS),
        parsed_object("d2", "drawing", created_t=BASE_MS + 1),
        parsed_object("m1", "mind-map-node", created_t=BASE_MS + 2, text="узел"),
        parsed_object("m2", "mind-map-node", created_t=BASE_MS + 3),
    ]

    signals, _ = _signals(objects)

    assert signals.n_drawings == 2
    assert signals.n_mind_map_nodes_with_text == 1


def test_updated_without_created_is_not_real_edit() -> None:
    objects = [
        parsed_object("a", created_t=None, updated_t=BASE_MS),
        parsed_object("b", created_t=BASE_MS, updated_t=None),
    ]

    signals, _ = _signals(objects)

    assert signals.n_real_edits == 0
    assert signals.n_objects_updated == 1


def test_comments_count_is_taken_from_parse_result() -> None:
    signals, _ = _signals(
        [parsed_object("a", created_t=BASE_MS)], comments=4
    )

    assert signals.n_comments == 4


def test_compute_signals_accepts_explicit_parse_result() -> None:
    objects = [parsed_object("a", created_t=BASE_MS, updated_t=BASE_MS + HOUR_MS)]
    parse_result = ParseResult(
        board_name="B",
        objects=objects,
        comments_count=0,
        is_truncated=True,
    )
    config = Config.defaults()

    signals = compute_signals(
        parse_result, detect_import_mode(objects, config), config
    )

    assert signals.n_real_edits == 1
    assert signals.threshold_real_ms == config.threshold_real_ms
