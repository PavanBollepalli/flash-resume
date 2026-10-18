"""SQLite application-history tracker for Flash Resume.

Stores metadata only (company, role, date, status, ats_score, filename pointer);
the PDF and diff.md bytes stay on disk in the output_dir. WAL mode lets the CLI
and the extension write concurrently without lock errors.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from flash_resume.config import get_config_dir

VALID_STATUSES = ("applied", "interview", "offered")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    jd_snippet TEXT,
    pdf_filename TEXT NOT NULL,
    ats_score INTEGER,
    mode TEXT DEFAULT 'truthful',
    source TEXT DEFAULT 'cli',
    status TEXT DEFAULT 'applied'
);
CREATE INDEX IF NOT EXISTS idx_created_at ON applications(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_company ON applications(company);
CREATE INDEX IF NOT EXISTS idx_status ON applications(status);
"""


def get_db_path() -> Path:
    """Return the path to history.db under ~/.config/flash-resume/."""
    return get_config_dir() / "history.db"


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or get_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """Create the schema (idempotent) and back-fill the status column if missing."""
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        # Back-fill `status` for databases created before the column existed.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(applications)")}
        if columns and "status" not in columns:
            conn.execute("ALTER TABLE applications ADD COLUMN status TEXT DEFAULT 'applied'")


def log_application(
    company: str,
    role: str,
    pdf_filename: str,
    jd_snippet: Optional[str] = None,
    ats_score: Optional[int] = None,
    mode: str = "truthful",
    source: str = "cli",
    status: str = "applied",
    db_path: Optional[Path] = None,
) -> int:
    """Insert one application row and return its id."""
    init_db(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO applications (company, role, jd_snippet, pdf_filename, ats_score, mode, source, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (company, role, jd_snippet, pdf_filename, ats_score, mode, source, status),
        )
        return int(cur.lastrowid)


def get_recent_applications(
    limit: int = 20,
    offset: int = 0,
    company_filter: Optional[str] = None,
    role_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """Return a paginated, filtered list of application rows (newest first)."""
    init_db(db_path)
    clauses, params = [], []
    if company_filter:
        clauses.append("company LIKE ?")
        params.append(f"%{company_filter}%")
    if role_filter:
        clauses.append("role LIKE ?")
        params.append(f"%{role_filter}%")
    if status_filter:
        if status_filter in VALID_STATUSES:
            clauses.append("status = ?")
            params.append(status_filter)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, created_at, company, role, jd_snippet, pdf_filename,
                   ats_score, mode, source, status
            FROM applications
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


def count_applications(
    company_filter: Optional[str] = None,
    role_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> int:
    """Return the number of rows matching the given filters."""
    init_db(db_path)
    clauses, params = [], []
    if company_filter:
        clauses.append("company LIKE ?")
        params.append(f"%{company_filter}%")
    if role_filter:
        clauses.append("role LIKE ?")
        params.append(f"%{role_filter}%")
    if status_filter:
        if status_filter in VALID_STATUSES:
            clauses.append("status = ?")
            params.append(status_filter)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _connect(db_path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM applications {where}", params).fetchone()[0])


def get_application_by_id(app_id: int, db_path: Optional[Path] = None) -> Optional[dict]:
    """Return one application row, or None."""
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, created_at, company, role, jd_snippet, pdf_filename, "
            "ats_score, mode, source, status FROM applications WHERE id = ?",
            (app_id,),
        ).fetchone()
    return dict(row) if row else None


def update_application_status(app_id: int, status: str, db_path: Optional[Path] = None) -> bool:
    """Set an application's pipeline status; returns True if a row was updated."""
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {', '.join(VALID_STATUSES)}")
    init_db(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE applications SET status = ? WHERE id = ?", (status, app_id)
        )
        return cur.rowcount > 0


def get_stats(
    company_filter: Optional[str] = None,
    role_filter: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> dict:
    """Return one-glance pipeline stats for the history dashboard.

    Totals span the *full* history (not just the current page), so the header
    stays meaningful as applications grow. `avg_ats` is over rows that have a
    score; those without are skipped so NULLs don't drag the average down.
    """
    init_db(db_path)
    clauses, params = [], []
    if company_filter:
        clauses.append("company LIKE ?")
        params.append(f"%{company_filter}%")
    if role_filter:
        clauses.append("role LIKE ?")
        params.append(f"%{role_filter}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with _connect(db_path) as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM applications {where}", params).fetchone()[0])
        by_status = {
            row["status"]: row["n"]
            for row in conn.execute(
                f"SELECT status, COUNT(*) AS n FROM applications {where} GROUP BY status",
                params,
            )
        }
        # Average over scored rows only; NULLs are skipped so they don't drag it down.
        avg_clauses = clauses + ["ats_score IS NOT NULL"]
        avg_where = "WHERE " + " AND ".join(avg_clauses)
        avg_row = conn.execute(
            f"SELECT AVG(ats_score) FROM applications {avg_where}", params
        ).fetchone()

    return {
        "total": total,
        "applied": by_status.get("applied", 0),
        "interview": by_status.get("interview", 0),
        "offered": by_status.get("offered", 0),
        "avg_ats": round(avg_row[0]) if avg_row and avg_row[0] is not None else None,
    }
