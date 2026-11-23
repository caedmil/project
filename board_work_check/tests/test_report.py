"""Тесты рендеринга отчётов."""

from __future__ import annotations

import json
from pathlib import Path

from board_work_check.config import Config
from board_work_check.import_detector import detect_import_mode
from board_work_check.parser import parse_backup
from board_work_check.report import render_json_report, render_text_report
from board_work_check.signals import compute_signals
from board_work_check.verdict import compute_verdict


def _build(path: Path, *, truncated: bool = False):
    config = Config.defaults()
    parsed = parse_backup(path)
    parsed.is_truncated = truncated
    import_mode = detect_import_mode(parsed.objects, config)
    signals = compute_signals(parsed, import_mode, config)
    verdict = compute_verdict(
        signals, import_mode, config, is_truncated=truncated
    )
    return parsed, import_mode, signals, verdict


def test_text_report_has_all_sections(full_board_path: Path) -> None:
    parsed, import_mode, signals, verdict = _build(full_board_path)

    text = render_text_report(parsed, import_mode, signals, verdict)

    assert "Доска: Тестовая доска: Практика" in text
    assert "ВЕРДИКТ: работа велась" in text
    assert "Уверенность: высокая" in text
    assert "Доказательства:" in text
    assert "Слабые места:" in text
    assert "Что не видно:" in text
    assert "Практика 18-20" in text
    assert "Режим: full" in text


def test_text_report_marks_truncated_file(full_board_path: Path) -> None:
    parsed, import_mode, signals, verdict = _build(full_board_path, truncated=True)

    text = render_text_report(parsed, import_mode, signals, verdict)

    assert "(файл обрезан)" in text
    assert "прочитаны не все объекты" in text


def test_text_report_imported_board_mentions_history(imported_board_path: Path) -> None:
    parsed, import_mode, signals, verdict = _build(imported_board_path)

    text = render_text_report(parsed, import_mode, signals, verdict)

    assert "Режим: post_import" in text
    assert "недоступна (доска импортирована)" in text


def test_color_is_optional(full_board_path: Path) -> None:
    parsed, import_mode, signals, verdict = _build(full_board_path)

    plain = render_text_report(parsed, import_mode, signals, verdict)
    colored = render_text_report(parsed, import_mode, signals, verdict, color=True)

    assert "\033[" not in plain
    assert "\033[32m" in colored


def test_json_report_is_serialisable(full_board_path: Path) -> None:
    parsed, import_mode, signals, verdict = _build(full_board_path)

    payload = render_json_report(parsed, import_mode, signals, verdict)
    serialised = json.dumps(payload, ensure_ascii=False)

    assert json.loads(serialised) == payload
    assert payload["tool"]["name"] == "board-work-check"
    assert payload["verdict"]["verdict"] == "work_detected"
    assert payload["signals"]["n_real_edits"] == 3
    assert payload["signals"]["n_filled_table_cells"] == 3
    assert "frames_with_edits" not in payload["signals"]
    assert payload["frames_with_edits"][0]["frame_id"] == "frame-1"
    assert payload["board"]["is_truncated"] is False


def test_json_report_contains_parser_warnings(
    imported_board_path: Path,
) -> None:
    parsed, import_mode, signals, verdict = _build(imported_board_path)

    payload = render_json_report(parsed, import_mode, signals, verdict)

    assert payload["parser"]["is_truncated"] is False
    assert payload["import_mode"]["mode"] == "post_import"
    assert payload["import_mode"]["explanation"]
