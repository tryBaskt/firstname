"""Atomic initial and new-listing batches backed by SQLite."""

import argparse
import fcntl
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .job_posts_sites.a16z import BOARD_URL, read_all_jobs

DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "data" / "jobs.sqlite3"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def setup_database(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT,
            apply_url TEXT,
            posted_at TEXT,
            first_seen_at TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (source, source_id)
        );
        CREATE INDEX IF NOT EXISTS jobs_company ON jobs(company);
        CREATE TABLE IF NOT EXISTS batch_runs (
            id INTEGER PRIMARY KEY,
            mode TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            source_total INTEGER NOT NULL,
            pages INTEGER NOT NULL,
            inserted INTEGER NOT NULL
        );
    """)


def check_mode(connection, mode):
    initialized = connection.execute("SELECT 1 FROM batch_runs WHERE mode = 'initial' LIMIT 1").fetchone() is not None
    if mode == "new" and not initialized:
        raise RuntimeError("Run the initial batch before the new-listings batch.")
    if mode == "initial" and initialized:
        raise RuntimeError("Initial import is already complete. Use the new batch.")


def save_batch(connection, mode, batch, started_at):
    check_mode(connection, mode)
    jobs = batch.get("jobs", [])
    if not jobs or len(jobs) != batch.get("total") or len({job["id"] for job in jobs}) != len(jobs):
        raise RuntimeError("Refusing to save an empty or incomplete batch.")
    completed_at = utc_now()
    inserted = 0
    with connection:
        for job in jobs:
            cursor = connection.execute("""
                INSERT INTO jobs (source, source_id, title, company, location, apply_url, posted_at, first_seen_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_id) DO NOTHING
            """, ("a16z", job["id"], job["title"], job["company_name"], job.get("location"),
                  job.get("apply_url"), job.get("posted_at"), completed_at,
                  json.dumps(job, ensure_ascii=False, separators=(",", ":"))))
            inserted += cursor.rowcount
        connection.execute("""
            INSERT INTO batch_runs(mode, started_at, completed_at, source_total, pages, inserted)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (mode, started_at, completed_at, batch["total"], batch["pages"], inserted))
    return {"mode": mode, "source": BOARD_URL, "source_total": batch["total"], "pages": batch["pages"],
            "inserted": inserted, "already_known": len(jobs) - inserted,
            "stored_total": connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], "completed_at": completed_at}


def run_batch(mode, database=DEFAULT_DATABASE, *, reader=read_all_jobs):
    database = Path(database).resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    with open(str(database) + ".lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another job import is already running.") from error
        with closing(sqlite3.connect(database)) as connection:
            setup_database(connection)
            check_mode(connection, mode)
            started_at = utc_now()

            def progress(companies, count, total):
                if companies % 10 == 0:
                    print(json.dumps({"companies_read": companies, "jobs_read": count, "total": total}), flush=True)

            batch = reader(progress=progress)
            return {"database": str(database), **save_batch(connection, mode, batch, started_at)}


def main():
    parser = argparse.ArgumentParser(description="FirstName a16z job batch importer")
    parser.add_argument("mode", choices=("initial", "new"))
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    try:
        print(json.dumps(run_batch(args.mode, args.database)), flush=True)
    except (RuntimeError, OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Batch failed: {error}\n")
