"""Определение режима доски по распределению ``created.t``.

Идея: если почти все объекты имеют одинаковый ``created.t``, значит доска
была загружена/скопирована одним пакетом, и время создания не отражает
реальную историю. Тогда единственный достоверный источник — ``updated``.
"""

from __future__ import annotations

from collections import Counter

from .config import Config
from .models import ImportMode, ParsedObject, ms_to_iso


def detect_import_mode(
    objects: list[ParsedObject],
    config: Config | None = None,
) -> ImportMode:
    """Классифицировать доску в ``full`` / ``post_import`` / ``mixed`` / ``unknown``."""
    config = config or Config.defaults()
    analysis = config.analysis

    created_ts = [obj.created_t for obj in objects if obj.created_t is not None]
    if not created_ts:
        return ImportMode(
            mode="unknown",
            top_created_t=None,
            top_share=0.0,
            n_unique_created=0,
            import_time_iso=None,
        )

    counter = Counter(created_ts)
    total = len(created_ts)
    top_t, top_count = counter.most_common(1)[0]
    top_share = top_count / total
    n_unique = len(counter)

    if top_share >= analysis.import_share_threshold:
        mode = "post_import"
    elif top_share <= analysis.full_share_threshold and n_unique >= max(
        analysis.unique_created_min, int(total * 0.1)
    ):
        mode = "full"
    else:
        mode = "mixed"

    return ImportMode(
        mode=mode,
        top_created_t=top_t,
        top_share=top_share,
        n_unique_created=n_unique,
        import_time_iso=(
            ms_to_iso(top_t, config.timezone_name) if mode != "full" else None
        ),
    )


def mode_reason(import_mode: ImportMode) -> str:
    """Короткое объяснение, почему режим именно такой (числа, не догадки)."""
    mode = import_mode.mode
    if mode == "unknown":
        return "нет ни одного объекта с валидным created.t"
    share = f"{import_mode.top_share * 100:.1f}%"
    if mode == "post_import":
        return (
            f"{share} объектов имеют одинаковый created.t — доска импортирована "
            "одним пакетом"
        )
    if mode == "full":
        return (
            f"самый частый created.t покрывает только {share} объектов "
            f"({import_mode.n_unique_created} уникальных значений) — "
            "время создания выглядит реальным"
        )
    return (
        f"самый частый created.t покрывает {share} объектов "
        f"({import_mode.n_unique_created} уникальных значений) — "
        "признаки импорта и ручного создания смешаны"
    )
