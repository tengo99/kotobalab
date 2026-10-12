import sqlite3

import pytest

from kotobalab.db import ConflictError, Repository
from kotobalab.history import comparison_rows
from kotobalab.models import Status
from kotobalab.samples import sample_question


@pytest.fixture
def q():
    return sample_question("N2", "문법", "이력 테스트")


@pytest.fixture
def repo(tmp_path):
    return Repository(tmp_path / "history.db")


def legacy_db(path, q):
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE questions (id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload TEXT NOT NULL,status TEXT NOT NULL,source TEXT NOT NULL,
            review_note TEXT NOT NULL,reviewer TEXT NOT NULL,approved_at TEXT,
            created_at TEXT NOT NULL,updated_at TEXT NOT NULL,revision INTEGER NOT NULL)""")
        conn.execute("INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (7, q.model_dump_json(), "승인 완료", "sample", "기존 메모", "기존 승인자",
                      "2026-10-01T00:00:00+00:00", "2026-09-01", "2026-10-01", 12))
        return tuple(conn.execute("SELECT * FROM questions").fetchone())


def test_migration_preserves_existing_rows_and_is_idempotent(tmp_path, q):
    path = tmp_path / "legacy.db"
    before = legacy_db(path, q)
    repo = Repository(path)
    with repo.connection() as conn:
        assert tuple(conn.execute("SELECT * FROM questions").fetchone()) == before
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    entry = repo.history(7)[0]
    assert entry["event"] == "baseline"
    assert entry["before_snapshot"] is None
    assert entry["to_revision"] == 12
    assert entry["editor"] == "알 수 없음"
    assert entry["after_snapshot"]["reviewer"] == "기존 승인자"
    assert Repository(path).history(7) == [entry]


def test_migration_rolls_back_on_history_failure(tmp_path, q):
    path = tmp_path / "legacy.db"
    before = legacy_db(path, q)

    class BrokenRepository(Repository):
        def _append_history(self, *args, **kwargs):
            raise RuntimeError("baseline failure")

    with pytest.raises(RuntimeError):
        BrokenRepository(path)
    with sqlite3.connect(path) as conn:
        assert tuple(conn.execute("SELECT * FROM questions").fetchone()) == before
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='question_history'").fetchone()


def test_creation_and_changes_store_complete_snapshots(repo, q):
    ident = repo.create(q, "sample")
    initial = repo.get(ident)
    changed = q.model_copy(update={"prompt": "新しい問題（　）。", "options": ["A", "B", "C", "D"],
                                   "answer": 2, "explanation": "새 정답 해설", "option_explanations": ["a", "b", "c", "d"]})
    repo.save(ident, changed, Status.NEEDS_REVISION, "재검수 요청", 1, editor="김편집", reason="복수 정답 해소")
    saved, created = repo.history(ident)
    assert created["event"] == "created"
    assert created["after_snapshot"]["question"] == q.model_dump()
    assert saved["before_snapshot"] == repo.snapshot(initial)
    assert saved["after_snapshot"] == repo.snapshot(repo.get(ident))
    assert saved["editor"] == "김편집"
    assert saved["reason"] == "복수 정답 해소"
    assert (saved["from_revision"], saved["to_revision"]) == (1, 2)
    assert saved["recorded_at"].endswith("+00:00")
    assert saved["content_changed"]
    fields = {row["항목"] for row in saved["changed_fields"]}
    assert {"문제", "선택지 1", "정답 번호", "정답 해설", "선택지 해설 1", "상태", "검수 메모"} <= fields
    assert Repository(repo.path).history(ident) == [saved, created]


def test_status_review_and_approval_records(repo, q):
    ident = repo.create(q, "sample")
    repo.save(ident, q, Status.APPROVED, "내용 확인", 1, confirmed=True, reviewer="승인자", editor="승인자", reason="최종 승인")
    approved = repo.history(ident)[0]
    assert approved["confirmed"]
    assert not approved["content_changed"]
    assert approved["after_snapshot"]["approved_at"]
    repo.save(ident, q.model_copy(update={"explanation": "수정한 해설"}), Status.NEEDS_REVISION,
              "재검수 필요", 2, editor="다른 편집자", reason="설명 오류 수정")
    revoked = repo.history(ident)[0]
    assert revoked["before_snapshot"]["status"] == "승인 완료"
    assert revoked["before_snapshot"]["reviewer"] == "승인자"
    assert revoked["after_snapshot"]["status"] == "초안"
    assert revoked["after_snapshot"]["approved_at"] is None
    assert repo.history(ident)[1] == approved


@pytest.mark.parametrize("kind", ["same", "surrounding_whitespace", "newlines", "dict_order"])
def test_no_substantive_change_keeps_version_and_approval(repo, q, kind):
    q = q.model_copy(update={"explanation": "첫 줄\n다음 줄"})
    ident = repo.create(q, "sample")
    repo.save(ident, q, Status.APPROVED, "검수 메모", 1, confirmed=True, reviewer="편집자")
    before = repo.get(ident)
    if kind == "surrounding_whitespace":
        attempted = q.model_copy(update={"prompt": "　" + q.prompt + " \n", "options": [" " + o + "　" for o in q.options]})
    elif kind == "newlines":
        attempted = q.model_copy(update={"explanation": "첫 줄\r\n다음 줄"})
    elif kind == "dict_order":
        attempted = type(q).model_validate(dict(reversed(list(q.model_dump().items()))))
    else:
        attempted = q
    assert repo.save(ident, attempted, Status.APPROVED, " 검수 메모　", 2, confirmed=True,
                     reviewer="편집자", editor="편집자", reason="변경 여부 확인") is False
    assert repo.get(ident) == before
    entry = repo.history(ident)[0]
    assert entry["event"] == "no_change"
    assert entry["from_revision"] == entry["to_revision"] == 2
    assert entry["changed_fields"] == []
    assert entry["submitted_snapshot"]["question"] == attempted.model_dump()


@pytest.mark.parametrize("kind", ["internal_space", "option_order", "answer", "individual_explanation"])
def test_meaningful_changes_revoke_approval(repo, q, kind):
    ident = repo.create(q, "sample")
    repo.save(ident, q, Status.APPROVED, "", 1, confirmed=True, reviewer="편집자")
    updates = {
        "internal_space": {"prompt": q.prompt[:2] + " " + q.prompt[2:]},
        "option_order": {"options": list(reversed(q.options))},
        "answer": {"answer": 2},
        "individual_explanation": {"option_explanations": ["변경된 설명"] + q.option_explanations[1:]},
    }
    repo.save(ident, q.model_copy(update=updates[kind]), Status.DRAFT, "", 2, editor="편집자")
    assert repo.get(ident)["status"] == "초안"
    assert repo.history(ident)[0]["content_changed"]


@pytest.mark.parametrize("event", ["created", "saved", "no_change"])
def test_history_insert_failure_rolls_back_question_transaction(repo, q, event):
    ident = repo.create(q, "sample")
    before, history = repo.get(ident), repo.history(ident)
    with repo.connection() as conn:
        conn.execute(f"""CREATE TRIGGER fail_history BEFORE INSERT ON question_history
            WHEN NEW.event='{event}' BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        if event == "created":
            repo.create(q, "sample")
        elif event == "saved":
            repo.save(ident, q.model_copy(update={"prompt": "변경 문제"}), Status.DRAFT, "", 1, editor="편집자")
        else:
            repo.save(ident, q, Status.DRAFT, "", 1, editor="편집자")
    assert repo.get(ident) == before
    assert repo.history(ident) == history
    assert len(repo.list()) == 1


def test_failed_approval_and_conflict_leave_no_history(repo, q):
    ident = repo.create(q, "sample")
    before = repo.history(ident)
    with pytest.raises(ValueError):
        repo.save(ident, q, Status.APPROVED, "", 1, reviewer="편집자")
    assert repo.history(ident) == before
    repo.save(ident, q, Status.NEEDS_REVISION, "검수", 1, editor="편집자")
    before = repo.history(ident)
    with pytest.raises(ConflictError):
        repo.save(ident, q, Status.DRAFT, "", 1, editor="편집자")
    assert repo.history(ident) == before


def test_history_is_append_only(repo, q):
    ident = repo.create(q, "sample")
    for sql in ("UPDATE question_history SET reason='덮어쓰기'", "DELETE FROM question_history"):
        with pytest.raises(sqlite3.IntegrityError):
            with repo.connection() as conn:
                conn.execute(sql)
    assert len(repo.history(ident)) == 1


def test_comparison_respects_positions_and_handles_incomplete_drafts(repo, q):
    ident = repo.create(q, "sample")
    old = repo.snapshot(repo.get(ident))
    new = {**old, "question": {**old["question"], "options": ["수정 선택지"], "option_explanations": []}}
    rows = comparison_rows(old, new, changed_only=True)
    assert any(r["항목"] == "선택지 2" and r["수정 후"] == "" for r in rows)
    assert any(r["항목"] == "선택지 해설 4" for r in rows)


def test_review_note_only_change_is_versioned(repo, q):
    ident = repo.create(q, "sample")
    repo.save(ident, q, Status.DRAFT, "문법 근거 확인 필요", 1, editor="편집자", reason="검수 기록 추가")
    entry = repo.history(ident)[0]
    assert entry["to_revision"] == 2
    assert not entry["content_changed"]
    assert [r["항목"] for r in entry["changed_fields"]] == ["검수 메모"]


def test_backup_script_preserves_rows_and_only_backs_up_before_first_migration(tmp_path, q, monkeypatch):
    from scripts import migrate_history

    folder = tmp_path / "data"
    folder.mkdir()
    before = legacy_db(folder / "kotobalab.db", q)
    monkeypatch.setattr(migrate_history, "ROOT", tmp_path)
    migrate_history.main()
    backups = list((folder / "backups").glob("*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        assert tuple(conn.execute("SELECT * FROM questions").fetchone()) == before
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    migrate_history.main()
    assert list((folder / "backups").glob("*.db")) == backups


def test_future_schema_is_rejected_without_modifying_data(tmp_path, q):
    path = tmp_path / "future.db"
    before = legacy_db(path, q)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=2")
    with pytest.raises(ValueError, match="새로운 DB"):
        Repository(path)
    with sqlite3.connect(path) as conn:
        assert tuple(conn.execute("SELECT * FROM questions").fetchone()) == before
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_blank_editor_is_rejected_without_record(repo, q):
    ident = repo.create(q, "sample")
    before = repo.history(ident)
    with pytest.raises(ValueError, match="편집자 이름"):
        repo.save(ident, q, Status.DRAFT, "", 1, editor="　 ")
    assert repo.history(ident) == before
