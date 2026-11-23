"""Тесты определения режима доски по распределению created.t."""

from __future__ import annotations

from board_work_check.config import Config
from board_work_check.import_detector import detect_import_mode
from helpers import parsed_object


def test_identical_created_t_is_post_import() -> None:
    objects = [parsed_object(f"o{index}", created_t=1_700_000_000_000.0) for index in range(10)]

    mode = detect_import_mode(objects)

    assert mode.mode == "post_import"
    assert mode.top_share == 1.0
    assert mode.n_unique_created == 1
    assert mode.top_created_t == 1_700_000_000_000.0
    assert mode.import_time_iso is not None


def test_spread_created_t_is_full() -> None:
    objects = [
        parsed_object(f"o{index}", created_t=1_700_000_000_000.0 + index * 3_600_000)
        for index in range(10)
    ]

    mode = detect_import_mode(objects)

    assert mode.mode == "full"
    assert mode.n_unique_created == 10
    assert mode.import_time_iso is None


def test_half_same_half_different_is_mixed() -> None:
    objects = [
        parsed_object("same-1", created_t=1_700_000_000_000.0),
        parsed_object("same-2", created_t=1_700_000_000_000.0),
        parsed_object("other-1", created_t=1_700_000_100_000.0),
        parsed_object("other-2", created_t=1_700_000_200_000.0),
    ]

    mode = detect_import_mode(objects)

    assert mode.mode == "mixed"
    assert mode.top_share == 0.5
    assert mode.import_time_iso is not None


def test_empty_list_is_unknown() -> None:
    mode = detect_import_mode([])

    assert mode.mode == "unknown"
    assert mode.top_created_t is None
    assert mode.top_share == 0.0
    assert mode.n_unique_created == 0
    assert mode.import_time_iso is None


def test_objects_without_created_t_ignored() -> None:
    objects = [parsed_object("a", created_t=None), parsed_object("b", created_t=None)]

    assert detect_import_mode(objects).mode == "unknown"


def test_custom_import_share_threshold_from_config() -> None:
    config = Config.model_validate({"analysis": {"import_share_threshold": 0.5}})
    objects = [
        parsed_object("same-1", created_t=1_700_000_000_000.0),
        parsed_object("same-2", created_t=1_700_000_000_000.0),
        parsed_object("other-1", created_t=1_700_000_100_000.0),
        parsed_object("other-2", created_t=1_700_000_200_000.0),
    ]

    mode = detect_import_mode(objects, config)

    assert mode.mode == "post_import"


def test_full_requires_enough_unique_values() -> None:
    # 2 уникальных created.t из 20 объектов (top_share = 0.5) — этого мало для full
    objects = [parsed_object(f"a{index}", created_t=1.0) for index in range(10)]
    objects += [parsed_object(f"b{index}", created_t=2.0) for index in range(10)]

    mode = detect_import_mode(objects)

    assert mode.mode == "mixed"
    assert mode.n_unique_created == 2
