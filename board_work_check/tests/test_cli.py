"""Тесты CLI: коды выхода, форматы вывода, сохранение JSON."""

from __future__ import annotations

import json
from pathlib import Path

from board_work_check.cli import main


def test_text_report_is_default(full_board_path: Path, capsys) -> None:
    code = main([str(full_board_path)])
    captured = capsys.readouterr()

    assert code == 0
    assert "ВЕРДИКТ: работа велась" in captured.out
    assert "Доска: Тестовая доска: Практика" in captured.out
    assert captured.err == ""


def test_json_output_is_valid(full_board_path: Path, capsys) -> None:
    code = main(["--output", "json", str(full_board_path)])
    captured = capsys.readouterr()

    payload = json.loads(captured.out)

    assert code == 0
    assert payload["verdict"]["verdict"] == "work_detected"
    assert payload["import_mode"]["mode"] == "full"
    assert payload["signals"]["n_real_edits"] == 3


def test_options_may_precede_path(full_board_path: Path, capsys) -> None:
    code = main(["--no-color", "--output", "text", str(full_board_path)])

    assert code == 0
    assert "ВЕРДИКТ" in capsys.readouterr().out


def test_missing_file_exit_code_2(workdir: Path) -> None:
    assert main([str(workdir / "absent.json")]) == 2


def test_directory_exit_code_2(workdir: Path) -> None:
    assert main([str(workdir)]) == 2


def test_broken_json_exit_code_1(workdir: Path) -> None:
    path = workdir / "broken.json"
    path.write_text("это не json", encoding="utf-8")

    assert main([str(path)]) == 1


def test_truncated_json_exit_code_0(workdir: Path, capsys) -> None:
    path = workdir / "cut.json"
    path.write_text(
        '{"boardName": "cut", "version": 1, "objects": ['
        '{"id": "a", "type": "sticker", "created": {"a": 1, "t": 1.0}}, '
        '{"id": "b", "type": "sti',
        encoding="utf-8",
    )

    code = main(["--output", "json", str(path)])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["board"]["is_truncated"] is True
    assert payload["board"]["objects_total"] == 1


def test_save_json_writes_file(full_board_path: Path, workdir: Path) -> None:
    target = workdir / "report.json"

    code = main(["--save-json", str(target), str(full_board_path)])

    assert code == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["verdict"]["verdict"] == "work_detected"


def test_save_json_bad_directory_exit_code_2(
    full_board_path: Path, workdir: Path
) -> None:
    target = workdir / "absent-dir" / "report.json"

    assert main(["--save-json", str(target), str(full_board_path)]) == 2


def test_verbose_keeps_stdout_valid_json(full_board_path: Path, capsys) -> None:
    code = main(["--verbose", "--output", "json", str(full_board_path)])
    captured = capsys.readouterr()

    payload = json.loads(captured.out)

    assert code == 0
    assert payload["board"]["objects_total"] == 13
    assert "сигналы" in captured.err
    assert "режим: full" in captured.err


def test_threshold_option_changes_signals(full_board_path: Path, capsys) -> None:
    code = main(
        ["--threshold-real-minutes", "600", "--output", "json", str(full_board_path)]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["signals"]["n_real_edits"] == 0
    assert payload["thresholds"]["threshold_real_minutes"] == 600.0


def test_invalid_threshold_exit_code_2(full_board_path: Path) -> None:
    assert main(["--threshold-real-minutes", "0", str(full_board_path)]) == 2


def test_config_option_applied(
    full_board_path: Path, workdir: Path, capsys
) -> None:
    config_path = workdir / "config.yaml"
    config_path.write_text("analysis:\n  timezone: UTC\n", encoding="utf-8")

    code = main(
        ["--config", str(config_path), "--output", "json", str(full_board_path)]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["import_mode"]["top_created_iso"].endswith("+00:00")


def test_invalid_config_exit_code_2(full_board_path: Path, workdir: Path) -> None:
    config_path = workdir / "config.yaml"
    config_path.write_text("analysis:\n  threshold_real_minutes: -5\n", encoding="utf-8")

    assert main(["--config", str(config_path), str(full_board_path)]) == 2


def test_imported_board_is_post_import(imported_board_path: Path, capsys) -> None:
    code = main(["--output", "json", str(imported_board_path)])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["import_mode"]["mode"] == "post_import"
    assert any(
        "история до импорта недоступна" in warning.lower()
        for warning in payload["verdict"]["warnings"]
    )
