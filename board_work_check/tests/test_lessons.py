from __future__ import annotations

from datetime import UTC, datetime

from board_work_check.config import Config
from board_work_check.lessons import SCHEDULE, analyze_lessons, infer_teacher_author, render_lesson_text
from board_work_check.models import ParseResult, ParsedObject


def ms(value: str) -> float:
    return datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1000


def obj(
    oid: str,
    kind: str,
    *,
    parent: str | None = None,
    created: str | None = None,
    updated: str | None = None,
    created_a: int | None = 10,
    updated_a: int | None = None,
    name: str | None = None,
    label: str | None = None,
) -> ParsedObject:
    return ParsedObject(
        object_id=oid,
        type=kind,
        parent_id=parent,
        created_a=created_a,
        created_t=ms(created) if created else None,
        updated_a=updated_a,
        updated_t=ms(updated) if updated else None,
        label=label or "Пара 17",
        file_name=name,
    )


def parsed(objects: list[ParsedObject]) -> ParseResult:
    return ParseResult(
        board_name="Тест",
        objects=objects,
        comments_count=0,
        is_truncated=False,
    )


def test_schedule_boundaries_are_start_inclusive_end_exclusive() -> None:
    from board_work_check.lessons import _lesson_slot

    tz = UTC
    assert _lesson_slot(datetime(2026, 10, 20, 8, 30, tzinfo=tz))[0] == 1
    assert _lesson_slot(datetime(2026, 10, 20, 10, 0, tzinfo=tz)) is None
    assert _lesson_slot(datetime(2026, 10, 20, 10, 15, tzinfo=tz))[0] == 2
    assert len(SCHEDULE) == 7


def test_detects_prepared_material_photo_and_post_lesson_review() -> None:
    frame = obj("f", "frame", created="2026-10-20T02:00:00")
    old = obj(
        "task", "simple-text", parent="f",
        created="2026-10-20T02:00:00", updated="2026-10-20T15:00:00",
        created_a=10, updated_a=10,
    )  # 07:00 prep; update at 20:00 local
    image = obj(
        "photo", "image", parent="f", created="2026-10-20T04:00:00",
        created_a=20, name="IMG_1234.jpg",
    )  # 09:00 local, student-like upload
    role_support = [
        obj(f"r{i}", "simple-text", created=f"2026-10-19T{i + 1:02}:00:00",
            updated=f"2026-10-19T{i + 1:02}:10:00", updated_a=10)
        for i in range(3)
    ]
    teacher, findings = analyze_lessons(parsed([frame, old, image, *role_support]))

    assert teacher.author_id == 10
    assert len(findings) == 1
    finding = findings[0]
    assert finding.slot_number == 1
    assert finding.preparation_iso is not None
    assert finding.image_uploads == 1
    assert finding.status == "review_signs_found"
    assert finding.reviewed_object_ids == ["task"]
    assert finding.confidence == "medium"


def test_image_only_does_not_count_as_review() -> None:
    frame = obj("f", "frame", created="2026-10-20T02:00:00")
    image = obj("i", "image", parent="f", created="2026-10-20T04:00:00", created_a=10)
    teacher, findings = analyze_lessons(parsed([frame, image]))
    assert teacher.author_id is None
    assert findings[0].status == "no_review_signs_found"
    assert findings[0].reviewed_object_ids == []
    assert findings[0].confidence == "low"


def test_new_image_updated_later_is_not_old_material_review() -> None:
    frame = obj("f", "frame", created="2026-10-20T02:00:00")
    image = obj(
        "i", "image", parent="f", created="2026-10-20T04:00:00",
        updated="2026-10-20T15:00:00", created_a=10, updated_a=10,
    )
    role_support = [
        obj(f"r{i}", "simple-text", created=f"2026-10-19T{i + 1:02}:00:00",
            updated=f"2026-10-19T{i + 1:02}:10:00", updated_a=10)
        for i in range(3)
    ]
    _, findings = analyze_lessons(parsed([frame, image, *role_support]))
    assert findings[0].status == "no_review_signs_found"


def test_student_author_update_does_not_count_as_teacher_review() -> None:
    frame = obj("f", "frame", created="2026-10-20T02:00:00")
    old = obj("task", "simple-text", parent="f", created="2026-10-20T02:00:00",
              updated="2026-10-20T15:00:00", created_a=10, updated_a=20)
    image = obj("i", "image", parent="f", created="2026-10-20T04:00:00", created_a=20)
    role_support = [
        obj(f"r{i}", "simple-text", created=f"2026-10-19T{i + 1:02}:00:00",
            updated=f"2026-10-19T{i + 1:02}:10:00", updated_a=10)
        for i in range(3)
    ]
    _, findings = analyze_lessons(parsed([frame, old, image, *role_support]))
    assert findings[0].status == "no_review_signs_found"


def test_teacher_author_override() -> None:
    guess = infer_teacher_author([], 300_000, override_author_id=42)
    assert guess.author_id == 42
    assert guess.status == "configured"


def test_mass_image_upload_does_not_infer_teacher_from_created_author() -> None:
    images = [
        obj(f"i{i}", "image", created="2026-10-20T04:00:00", created_a=42)
        for i in range(20)
    ]
    guess = infer_teacher_author(images, 300_000)
    assert guess.author_id is None


def test_no_photo_in_class_means_no_lesson_finding() -> None:
    frame = obj("f", "frame", created="2026-10-20T02:00:00")
    text = obj("t", "simple-text", parent="f", created="2026-10-20T02:00:00")
    _, findings = analyze_lessons(parsed([frame, text]))
    assert findings == []


def test_follow_up_window_is_configurable() -> None:
    frame = obj("f", "frame", created="2026-10-20T02:00:00")
    old = obj("task", "simple-text", parent="f", created="2026-10-20T02:00:00",
              updated="2026-10-21T03:00:00", created_a=10, updated_a=10)
    image = obj("i", "image", parent="f", created="2026-10-20T04:00:00", created_a=20)
    role_support = [
        obj(f"r{i}", "simple-text", created=f"2026-10-19T{i + 1:02}:00:00",
            updated=f"2026-10-19T{i + 1:02}:10:00", updated_a=10)
        for i in range(3)
    ]
    _, short = analyze_lessons(parsed([frame, old, image, *role_support]), follow_up_hours=12)
    _, full = analyze_lessons(parsed([frame, old, image, *role_support]), follow_up_hours=24)
    assert short[0].status == "no_review_signs_found"
    assert full[0].status == "review_signs_found"


def test_render_groups_imported_ranges_and_sorts_remaining_lessons() -> None:
    frame9 = obj("f9", "frame", created="2026-10-20T02:00:00", label="Практика 9 - тема")
    image9 = obj("i9", "image", parent="f9", created="2026-10-20T04:00:00", created_a=20)
    frame10 = obj("f10", "frame", created="2026-10-20T02:00:00", label="Практика 10 - тема")
    image10 = obj("i10", "image", parent="f10", created="2026-10-20T04:00:00", created_a=20)
    frame11 = obj("f11", "frame", created="2026-10-20T02:00:00", label="Практика 11 - тема")
    image11 = obj("i11", "image", parent="f11", created="2026-10-20T04:00:00", created_a=20)
    frame12 = obj("f12", "frame", created="2026-10-20T02:00:00", label="Практика 12 - тема")
    image12 = obj("i12", "image", parent="f12", created="2026-10-20T04:00:00", created_a=20)
    # Exactly three identical create timestamps on these lesson frames imply imported content.
    imported9 = [obj(f"a{i}", "simple-text", parent="f9", created="2026-10-19T01:00:00") for i in range(30)]
    imported10 = [obj(f"b{i}", "simple-text", parent="f10", created="2026-10-19T01:00:00") for i in range(30)]
    lesson_frames = [frame12, frame10, frame9, frame11]
    teacher, findings = analyze_lessons(
        parsed([*lesson_frames, image9, image10, image11, image12, *imported9, *imported10]),
        Config.defaults(),
    )
    report = render_lesson_text(
        parsed([*lesson_frames, image9, image10, image11, image12, *imported9, *imported10]),
        teacher,
        findings,
    )
    imported_line = "Пары 9-10 импортированы, их не анализируем."
    assert imported_line in report
    assert "Пара 11-12 не импортирована." in report
    assert report.index(imported_line) < report.index("Пара 11-12 не импортирована.")
    assert report.count("Пары 9-10 импортированы") == 1


def test_import_like_frame_caps_confidence() -> None:
    frame = obj("f", "frame", created="2026-10-20T02:00:00")
    # One dominant timestamp for the frame subtree denotes an import-like batch.
    old = obj("task", "simple-text", parent="f", created="2026-10-19T02:00:00",
              updated="2026-10-20T15:00:00", created_a=10, updated_a=10)
    image = obj("i", "image", parent="f", created="2026-10-20T04:00:00", created_a=20)
    support = [
        obj(f"r{i}", "simple-text", parent="f", created="2026-10-20T02:00:00")
        for i in range(20)
    ]
    role_support = [
        obj(f"x{i}", "simple-text", created=f"2026-10-19T{i + 1:02}:00:00",
            updated=f"2026-10-19T{i + 1:02}:10:00", updated_a=10)
        for i in range(3)
    ]
    _, findings = analyze_lessons(parsed([frame, old, image, *support, *role_support]))
    assert findings[0].status == "not_analyzable_imported"
    assert findings[0].confidence == "low"
