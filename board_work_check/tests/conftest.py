"""Общие фикстуры pytest."""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
_TEST_TMP = Path(__file__).parent / ".test-tmp"


@pytest.fixture
def workdir() -> Iterator[Path]:
    """Временный каталог для одного теста.

    Встроенный ``tmp_path`` здесь не используется: pytest создаёт свой
    basetemp с правами ``0o700``, а в Windows-песочнице такой каталог
    потом нельзя прочитать, и pytest падает на этапе собственной очистки.
    Каталог ниже создаётся с обычными правами и удаляется после теста.
    """
    _TEST_TMP.mkdir(parents=True, exist_ok=True)
    path = _TEST_TMP / f"case-{uuid.uuid4().hex[:12]}"
    os.makedirs(path)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def full_board_path() -> Path:
    """Доска с реальной историей: режим full, 3 «настоящие» правки."""
    return FIXTURES_DIR / "minimal_full.json"


@pytest.fixture
def imported_board_path() -> Path:
    """Импортированная доска: все created.t одинаковые."""
    return FIXTURES_DIR / "minimal_imported.json"
