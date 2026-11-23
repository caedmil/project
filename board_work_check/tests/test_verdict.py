"""Тесты правил вердикта и уверенности."""

from __future__ import annotations

from board_work_check.config import Config
from board_work_check.models import FrameWithEdits, ImportMode
from board_work_check.verdict import compute_verdict
from helpers import make_signals


def _mode(name: str, **overrides) -> ImportMode:
    params = {
        "mode": name,
        "top_created_t": 1_700_000_000_000.0 if name == "post_import" else None,
        "top_share": 1.0 if name == "post_import" else 0.1,
        "n_unique_created": 1 if name == "post_import" else 10,
        "import_time_iso": "2026-10-14T13:30:47+05:00" if name == "post_import" else None,
    }
    params.update(overrides)
    return ImportMode(**params)


def _frame(frame_id: str = "f1") -> FrameWithEdits:
    return FrameWithEdits(
        frame_id=frame_id,
        label="Практика",
        last_edit_iso="2026-10-18T12:46:00+05:00",
        delta_hours=12.0,
        edited_object_ids=["c1"],
    )


def test_no_signals_is_no_work_detected() -> None:
    verdict = compute_verdict(make_signals(), _mode("post_import"))

    assert verdict.verdict == "no_work_detected"
    assert verdict.warnings


def test_three_real_edits_is_work_detected() -> None:
    signals = make_signals(
        n_objects_total=10,
        n_real_edits=3,
        n_objects_updated=3,
        n_unique_updated_authors=1,
    )

    verdict = compute_verdict(signals, _mode("full"))

    assert verdict.verdict == "work_detected"
    assert any("3" in reason for reason in verdict.reasons)


def test_single_real_edit_is_ambiguous() -> None:
    signals = make_signals(
        n_objects_total=10, n_real_edits=1, n_objects_updated=1
    )

    verdict = compute_verdict(signals, _mode("full"))

    assert verdict.verdict == "ambiguous"
    assert verdict.confidence == "medium"


def test_post_import_adds_history_warning() -> None:
    signals = make_signals(n_objects_total=10, n_real_edits=3)

    verdict = compute_verdict(signals, _mode("post_import"))

    assert any(
        "история до импорта недоступна" in warning.lower()
        for warning in verdict.warnings
    )


def test_mixed_mode_downgrades_high_confidence() -> None:
    signals = make_signals(
        n_objects_total=10,
        n_real_edits=5,
        n_objects_updated=5,
        n_unique_updated_authors=2,
        n_filled_table_cells=3,
        n_stickers_with_text=4,
        frames_with_edits=[_frame()],
    )

    verdict = compute_verdict(signals, _mode("mixed"))

    assert verdict.verdict == "work_detected"
    assert verdict.confidence == "medium"


def test_full_mode_with_three_strong_signals_is_high() -> None:
    signals = make_signals(
        n_objects_total=10,
        n_real_edits=5,
        n_objects_updated=5,
        n_unique_updated_authors=1,
        n_filled_table_cells=3,
        n_stickers_with_text=4,
        frames_with_edits=[_frame()],
    )

    verdict = compute_verdict(signals, _mode("full"))

    assert verdict.confidence == "high"


def test_full_mode_spread_over_24_hours_is_work() -> None:
    signals = make_signals(
        n_objects_total=20,
        n_stickers_with_text=5,
        created_spread_hours=30.0,
    )

    verdict = compute_verdict(signals, _mode("full"))

    assert verdict.verdict == "work_detected"
    assert any("30" in reason or "1.2" in reason for reason in verdict.reasons)


def test_filled_cells_with_single_edit_is_work() -> None:
    signals = make_signals(
        n_objects_total=10,
        n_real_edits=1,
        n_filled_table_cells=2,
    )

    verdict = compute_verdict(signals, _mode("mixed"))

    assert verdict.verdict == "work_detected"


def test_truncated_file_adds_warning() -> None:
    signals = make_signals(n_objects_total=10, n_real_edits=3)

    verdict = compute_verdict(signals, _mode("full"), is_truncated=True)

    assert any("обрезан" in warning.lower() for warning in verdict.warnings)


def test_no_work_mentions_uploaded_files() -> None:
    signals = make_signals(n_objects_total=50, n_files=40, n_empty_stickers=10)

    verdict = compute_verdict(signals, _mode("post_import"))

    assert verdict.verdict == "no_work_detected"
    assert any("40" in reason and "файл" in reason for reason in verdict.reasons)


def test_many_warnings_drop_confidence_to_low() -> None:
    signals = make_signals(n_objects_total=0)
    verdict = compute_verdict(signals, _mode("mixed"), is_truncated=True)

    assert verdict.confidence == "low"


def test_n_during_min_from_config() -> None:
    config = Config.model_validate({"analysis": {"classification": {"n_during_min": 5}}})
    signals = make_signals(n_objects_total=10, n_real_edits=3)

    verdict = compute_verdict(signals, _mode("full"), config)

    assert verdict.verdict == "ambiguous"


def test_reasons_have_no_vague_words() -> None:
    signals = make_signals(
        n_objects_total=10,
        n_real_edits=4,
        n_unique_updated_authors=1,
        frames_with_edits=[_frame()],
        n_filled_table_cells=2,
        n_stickers_with_text=3,
        n_drawings=1,
    )

    verdict = compute_verdict(signals, _mode("full"))

    text = " ".join(verdict.reasons).lower()
    assert "примерно" not in text
    assert "вероятно" not in text
