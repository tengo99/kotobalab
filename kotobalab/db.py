import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from kotobalab.models import Question, Status, format_errors
from kotobalab.history import comparison_rows, normalized, same_content


class ConflictError(ValueError):
    pass


class Repository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise ValueError("현재 앱보다 새로운 DB 구조입니다. 데이터는 변경하지 않았습니다.")
            conn.execute("""CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '초안'
                    CHECK(status IN ('초안','수정 필요','승인 완료')),
                source TEXT NOT NULL,
                review_note TEXT NOT NULL DEFAULT '',
                reviewer TEXT NOT NULL DEFAULT '',
                approved_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS question_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL REFERENCES questions(id),
                from_revision INTEGER,
                to_revision INTEGER NOT NULL,
                event TEXT NOT NULL CHECK(event IN ('created','baseline','saved','no_change')),
                editor TEXT NOT NULL,
                reason TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                confirmed INTEGER NOT NULL,
                content_changed INTEGER NOT NULL,
                before_snapshot TEXT,
                after_snapshot TEXT NOT NULL,
                submitted_snapshot TEXT,
                changed_fields TEXT NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS history_question_idx ON question_history(question_id,id DESC)")
            conn.execute("""CREATE TRIGGER IF NOT EXISTS history_no_update BEFORE UPDATE ON question_history
                BEGIN SELECT RAISE(ABORT, '수정 이력은 변경할 수 없습니다.'); END""")
            conn.execute("""CREATE TRIGGER IF NOT EXISTS history_no_delete BEFORE DELETE ON question_history
                BEGIN SELECT RAISE(ABORT, '수정 이력은 삭제할 수 없습니다.'); END""")
            # Existing versions are kept exactly as stored. Past versions cannot be reconstructed.
            for row in conn.execute("""SELECT * FROM questions q WHERE NOT EXISTS
                    (SELECT 1 FROM question_history h WHERE h.question_id=q.id)""").fetchall():
                record = self.decode(row)
                self._append_history(conn, None, record, "baseline", "알 수 없음",
                                     "이력 기능 도입 시점의 기존 문항. 이전 수정 과정은 기록되어 있지 않습니다.")
            conn.execute("PRAGMA user_version=1")

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def create(self, question: Question, source: str) -> int:
        # All newly generated questions start as drafts, even if format is valid.
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = self.now()
            cursor = conn.execute(
                "INSERT INTO questions(payload,status,source,created_at,updated_at) VALUES (?,?,?,?,?)",
                (question.model_dump_json(), Status.DRAFT.value, source, now, now),
            )
            ident = cursor.lastrowid
            record = self.decode(conn.execute("SELECT * FROM questions WHERE id=?", (ident,)).fetchone())
            self._append_history(conn, None, record, "created", "AI 생성" if source == "openai" else "샘플 생성", "초안 생성")
            return ident

    @staticmethod
    def decode(row):
        if row is None:
            raise ValueError("문항을 찾을 수 없습니다.")
        result = dict(row)
        result["question"] = Question.model_validate(json.loads(result.pop("payload")))
        return result

    def get(self, question_id: int):
        with self.connection() as conn:
            return self.decode(conn.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone())

    def list(self):
        with self.connection() as conn:
            return [self.decode(row) for row in conn.execute("SELECT * FROM questions ORDER BY id DESC")]

    @staticmethod
    def snapshot(record):
        return {**record, "question": record["question"].model_dump()}

    def _append_history(self, conn, before, after, event, editor, reason, *, confirmed=False,
                        content_changed=False, submitted=None):
        old = self.snapshot(before) if before else None
        new = self.snapshot(after)
        encode = lambda value: json.dumps(value, ensure_ascii=False)
        conn.execute("""INSERT INTO question_history(question_id,from_revision,to_revision,event,
            editor,reason,recorded_at,confirmed,content_changed,before_snapshot,after_snapshot,
            submitted_snapshot,changed_fields) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (after["id"], before["revision"] if before else None, after["revision"], event,
             editor, reason, self.now(), int(confirmed), int(content_changed),
             encode(old) if old else None, encode(new),
             encode(self.snapshot(submitted)) if submitted else None,
             encode(comparison_rows(old, new, changed_only=True))))

    def history(self, question_id: int):
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM question_history WHERE question_id=? ORDER BY id DESC", (question_id,)).fetchall()
            result = []
            for row in rows:
                entry = dict(row)
                for key in ("before_snapshot", "after_snapshot", "submitted_snapshot", "changed_fields"):
                    entry[key] = json.loads(entry[key]) if entry[key] is not None else None
                result.append(entry)
            return result

    def save(self, question_id: int, question: Question, status: Status, review_note: str,
             revision: int, *, confirmed: bool = False, reviewer: str = "",
             editor: str | None = None, reason: str = ""):
        status = Status(status)
        errors = format_errors(question)
        if status == Status.APPROVED:
            if errors:
                raise ValueError("형식 오류를 해결한 후 승인할 수 있습니다: " + " / ".join(errors))
            if not confirmed or not reviewer.strip():
                raise ValueError("편집자 이름과 일본어 내용 검수에 대한 명시적 확인이 필요합니다.")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self.decode(conn.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone())
            if current["revision"] != revision:
                raise ConflictError("다른 창에서 문항이 변경되었습니다. 새로고침한 후 다시 검수하세요.")
            actor = editor.strip() if editor is not None else reviewer.strip() or "미기록(기존 호출)"
            if not actor:
                raise ValueError("저장할 편집자 이름을 입력하세요.")
            changed = not same_content(current["question"], question)
            if changed and current["status"] == Status.APPROVED.value and status == Status.APPROVED:
                raise ValueError("승인된 문항을 수정하면 먼저 초안 또는 수정 필요로 저장한 뒤 다시 승인하세요.")
            if changed and current["status"] == Status.APPROVED.value:
                status = Status.DRAFT
            # Ignore cosmetic edits, preserving the exact stored content and approval.
            effective_question = question if changed else current["question"]
            effective_note = review_note if normalized(review_note) != normalized(current["review_note"]) else current["review_note"]
            effective_reviewer = reviewer.strip() if status == Status.APPROVED else ""
            if (not changed and status.value == current["status"] and effective_note == current["review_note"]
                    and effective_reviewer == current["reviewer"]):
                submitted = {**current, "question": question, "review_note": review_note}
                self._append_history(conn, current, current, "no_change", actor, reason.strip(),
                                     confirmed=confirmed, submitted=submitted)
                return False
            now = self.now()
            cursor = conn.execute("""UPDATE questions SET payload=?,status=?,review_note=?,reviewer=?,
                approved_at=?,updated_at=?,revision=revision+1 WHERE id=? AND revision=?""",
                (effective_question.model_dump_json(), status.value, effective_note,
                 effective_reviewer,
                 now if status == Status.APPROVED else None, now, question_id, revision))
            if cursor.rowcount != 1:
                raise ConflictError("다른 창에서 문항이 변경되었습니다. 새로고침하세요.")
            after = self.decode(conn.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone())
            self._append_history(conn, current, after, "saved", actor, reason.strip(),
                                 confirmed=confirmed, content_changed=changed)
            return True
