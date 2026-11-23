"""CLI-интерфейс ``board-work-check``.

Весь диагностический вывод идёт в stderr, поэтому ``--output json`` всегда
оставляет stdout валидным JSON.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import click

from . import __version__
from .config import Config, ConfigError
from .import_detector import detect_import_mode, mode_reason
from .models import ImportMode, ParseResult, Signals, ms_to_iso
from .parser import ParseError, parse_backup
from .report import render_json_report, render_text_report
from .signals import compute_signals
from .verdict import compute_verdict

EXIT_OK = 0
EXIT_PARSE_ERROR = 1
EXIT_FILE_ERROR = 2


def _fail(message: str, code: int) -> int:
    click.echo(f"board-work-check: {message}", err=True)
    return code


def _configure_output() -> None:
    """Не падать, если консоль не умеет кодировать символы из отчёта.

    На русской Windows stdout открыт в cp1251, а в названиях досок и
    подписях фреймов может встретиться что угодно, включая эмодзи.
    Заменяем непечатаемые символы на «?» вместо исключения.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):  # pragma: no cover - поток уже закрыт
            continue


def _use_color(no_color: bool) -> bool:
    if no_color:
        return False
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):  # pragma: no cover - экзотический stdout
        return False


def _print_diagnostics(
    parsed: ParseResult,
    import_mode: ImportMode,
    signals: Signals,
    config: Config,
) -> None:
    def note(message: str) -> None:
        click.echo(f"[verbose] {message}", err=True)

    note(
        f"источник: {parsed.source_path} ({parsed.source_kind}, "
        f"{parsed.size_bytes / 1_048_576:.2f} МБ)"
    )
    note(
        f"объектов: {signals.n_objects_total}, комментариев: "
        f"{signals.n_comments}, обрезан: {parsed.is_truncated}"
    )
    if import_mode.top_created_t is not None:
        created_iso = ms_to_iso(import_mode.top_created_t, config.timezone_name)
        note(
            f"режим: {import_mode.mode}; top_share={import_mode.top_share:.4f}; "
            f"уникальных created.t={import_mode.n_unique_created}; "
            f"top_created={import_mode.top_created_t} ({created_iso})"
        )
    else:
        note(f"режим: {import_mode.mode}; created.t отсутствуют")
    note(f"почему режим: {mode_reason(import_mode)}")
    if parsed.warnings:
        note("warnings парсера:")
        for warning in parsed.warnings:
            note(f"  * {warning}")
    note("сигналы:")
    for key, value in asdict(signals).items():
        if key == "frames_with_edits":
            note(f"  {key}: {len(value)}")
            for frame in value[:10]:
                note(
                    f"    - {frame['frame_id']} «{frame['label']}» "
                    f"last_edit={frame['last_edit_iso']} "
                    f"delta_hours={frame['delta_hours']} "
                    f"edited={len(frame['edited_object_ids'])}"
                )
        else:
            note(f"  {key}: {value}")


@click.command(
    name="board-work-check",
    help=(
        "Оценить по JSON-бэкапу доски, вёл ли преподаватель работу с контентом. "
        "Вход: JSON-файл бэкапа или ZIP-архив экспорта (.holst/.rtb) с data.json."
    ),
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.argument("path", type=click.Path(path_type=Path, exists=False, dir_okay=False))
@click.option(
    "--threshold-real-minutes",
    type=float,
    default=None,
    metavar="MINUTES",
    help=(
        "Порог «настоящей» правки: updated - created > MINUTES. "
        "По умолчанию берётся из config.yaml (5 мин)."
    ),
)
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Формат отчёта в stdout.",
)
@click.option(
    "--save-json",
    "save_json_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    metavar="PATH",
    help="Дополнительно сохранить JSON-отчёт в файл.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    metavar="PATH",
    help="Путь к config.yaml (по умолчанию — значения из кода).",
)
@click.option(
    "--no-color", is_flag=True, help="Отключить ANSI-раскраску вывода."
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Печатать warnings парсера и промежуточные сигналы (в stderr).",
)
@click.version_option(__version__, "-V", "--version", prog_name="board-work-check")
def cli_command(
    path: Path,
    threshold_real_minutes: float | None,
    output_format: str,
    save_json_path: Path | None,
    config_path: Path | None,
    no_color: bool,
    verbose: bool,
) -> int:
    """Точка входа команды; возвращает код выхода (0/1/2)."""
    _configure_output()
    if not path.exists():
        return _fail(f"файл не найден: {path}", EXIT_FILE_ERROR)
    if path.is_dir():
        return _fail(f"путь указывает на каталог, а нужен файл: {path}", EXIT_FILE_ERROR)

    try:
        config = Config.load(config_path)
    except ConfigError as exc:
        return _fail(f"ошибка конфигурации: {exc}", EXIT_FILE_ERROR)

    if threshold_real_minutes is not None:
        try:
            config = config.with_threshold_minutes(threshold_real_minutes)
        except ConfigError as exc:
            return _fail(str(exc), EXIT_FILE_ERROR)

    if verbose and not config.timezone_is_available():
        click.echo(
            f"[verbose] таймзона {config.timezone_name} недоступна в системе, "
            "даты выводятся в UTC",
            err=True,
        )

    try:
        parsed = parse_backup(path)
    except ParseError as exc:
        return _fail(f"не удалось разобрать бэкап: {exc}", EXIT_PARSE_ERROR)
    except FileNotFoundError:
        return _fail(f"файл не найден: {path}", EXIT_FILE_ERROR)
    except OSError as exc:
        return _fail(f"ошибка чтения файла: {exc}", EXIT_FILE_ERROR)

    import_mode = detect_import_mode(parsed.objects, config)
    signals = compute_signals(parsed, import_mode, config)
    verdict = compute_verdict(
        signals, import_mode, config, is_truncated=parsed.is_truncated
    )

    if verbose:
        _print_diagnostics(parsed, import_mode, signals, config)

    if output_format == "json":
        payload = render_json_report(
            parsed, import_mode, signals, verdict, timezone=config.timezone_name
        )
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        click.echo(
            render_text_report(
                parsed,
                import_mode,
                signals,
                verdict,
                timezone=config.timezone_name,
                color=_use_color(no_color),
            )
        )

    if save_json_path is not None:
        try:
            payload = render_json_report(
                parsed,
                import_mode,
                signals,
                verdict,
                timezone=config.timezone_name,
            )
            save_json_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            return _fail(f"не удалось сохранить JSON-отчёт: {exc}", EXIT_FILE_ERROR)
        if verbose:
            click.echo(f"[verbose] JSON-отчёт сохранён: {save_json_path}", err=True)

    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Обёртка для ``[project.scripts]`` и ``python -m board_work_check``."""
    _configure_output()
    try:
        result = cli_command.main(
            args=list(argv) if argv is not None else None,
            prog_name="board-work-check",
            standalone_mode=False,
        )
    except click.ClickException as exc:
        exc.show()
        return EXIT_FILE_ERROR
    except click.exceptions.Exit as exc:  # --help / --version
        return int(exc.exit_code)
    except SystemExit as exc:  # страховка от sys.exit внутри click
        return int(exc.code or 0)
    except click.Abort:
        return 130
    return int(result) if result is not None else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
