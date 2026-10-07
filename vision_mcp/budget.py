"""Persistent atomic call reservation. Failures still count; no automatic retry."""
import sqlite3
import time
from contextlib import closing
from pathlib import Path


class BudgetError(ValueError):
    pass


def reserve(path: Path, daily: int, window: int) -> None:
    now = time.time()
    day_start = int(now // 86400) * 86400  # UTC, documented in README
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS calls (created REAL NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM calls WHERE created < ?", (min(day_start, now - 600),))
        day_count = db.execute("SELECT count(*) FROM calls WHERE created >= ?", (day_start,)).fetchone()[0]
        recent = db.execute("SELECT count(*) FROM calls WHERE created > ?", (now - 600,)).fetchone()[0]
        if day_count >= daily or recent >= window:
            raise BudgetError("API呼び出し回数の上限です。この依頼で再試行しないでください。")
        db.execute("INSERT INTO calls VALUES (?)", (now,))
