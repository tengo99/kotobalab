"""Back up an existing project DB, then migrate without modifying question rows."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kotobalab.db import Repository


def main():
    path = ROOT / "data" / "kotobalab.db"
    before = None
    if path.exists():
        with sqlite3.connect(path) as source:
            version = source.execute("PRAGMA user_version").fetchone()[0]
            before = source.execute("SELECT * FROM questions ORDER BY id").fetchall()
            if version == 0:
                folder = ROOT / "data" / "backups"
                folder.mkdir(exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup = folder / f"kotobalab_pre_history_{stamp}.db"
                with sqlite3.connect(backup) as target:
                    source.backup(target)
                print(f"Backup: {backup}")
    repo = Repository(path)
    with repo.connection() as conn:
        after = conn.execute("SELECT * FROM questions ORDER BY id").fetchall()
        if before is not None and [tuple(row) for row in after] != before:
            raise RuntimeError("Question row preservation check failed")
        print(f"Migration complete: {len(after)} questions preserved; schema version 1")


if __name__ == "__main__":
    main()
