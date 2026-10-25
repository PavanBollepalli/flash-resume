"""Tests for the SQLite application-history tracker and its API endpoints."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import flash_resume.services.tracking as tracking
import flash_resume.services.server as server


@pytest.fixture
def db(tmp_path: Path):
    """Isolate history into a temp file per test."""
    db_path = tmp_path / "history.db"
    original = tracking.get_db_path
    tracking.get_db_path = lambda: db_path
    yield db_path
    tracking.get_db_path = original


@pytest.fixture
def client(db: Path, tmp_path: Path):
    """TestClient with output_dir pointed at a temp artifacts folder."""
    out = tmp_path / "FlashResume"
    out.mkdir()
    (out / "2026-10-17-1432_Stripe_Senior_SWE.pdf").write_bytes(b"%PDF-1.4 fake")
    (out / "2026-10-17-1432_Stripe_Senior_SWE.diff.md").write_text(
        "# Diff report\n- added detail\n", encoding="utf-8"
    )

    original_load = server.load_config
    server.load_config = lambda: type("C", (), {"output_dir": str(out)})()
    yield TestClient(server.app)
    server.load_config = original_load


def test_log_and_rank_newest_first(db: Path):
    tracking.log_application("Stripe", "Senior SWE", "stripe.pdf", ats_score=88, source="extension")
    tracking.log_application("Google", "Staff Eng", "google.pdf", ats_score=91, status="interview")

    apps = tracking.get_recent_applications()
    assert [a["company"] for a in apps] == ["Google", "Stripe"]
    assert tracking.count_applications() == 2


def test_filters(db: Path):
    tracking.log_application("Stripe", "Senior SWE", "stripe.pdf", ats_score=88)
    tracking.log_application("Google", "Staff Eng", "google.pdf", ats_score=91, status="interview")
    tracking.log_application("Wellfound", "PM", "wellfound.pdf", ats_score=None)

    assert [a["company"] for a in tracking.get_recent_applications(company_filter="Strip")] == ["Stripe"]
    assert [a["company"] for a in tracking.get_recent_applications(status_filter="interview")] == ["Google"]
    assert [a["company"] for a in tracking.get_recent_applications(role_filter="Staff")] == ["Google"]


def test_status_update_validation(db: Path):
    app_id = tracking.log_application("Stripe", "Senior SWE", "stripe.pdf")
    assert tracking.update_application_status(app_id, "offered") is True
    assert tracking.get_application_by_id(app_id)["status"] == "offered"
    assert tracking.update_application_status(9999, "offered") is False

    with pytest.raises(ValueError):
        tracking.update_application_status(app_id, "bogus")


def test_history_endpoint(client: TestClient, db: Path):
    tracking.log_application(
        "Stripe", "Senior SWE", "2026-10-17-1432_Stripe_Senior_SWE.pdf",
        ats_score=88, source="extension",
    )
    res = client.get("/api/history?limit=5")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    app = body["applications"][0]
    assert app["company"] == "Stripe"
    # created_at defaults to CURRENT_TIMESTAMP, so assert against today rather
    # than a pinned date — otherwise this breaks every time the day rolls over.
    assert app["date"] == date.today().isoformat()
    assert app["status"] == "applied"


def test_resume_and_diff_endpoints(client: TestClient, db: Path):
    app_id = tracking.log_application(
        "Stripe", "Senior SWE", "2026-10-17-1432_Stripe_Senior_SWE.pdf", ats_score=88,
    )

    res = client.get(f"/api/resume/{app_id}")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF-1.4")

    res = client.get(f"/api/resume/{app_id}/diff")
    assert res.status_code == 200
    assert res.text.startswith("# Diff report")

    assert client.get("/api/resume/9999").status_code == 404


def test_patch_status_endpoint(client: TestClient, db: Path):
    app_id = tracking.log_application("Stripe", "Senior SWE", "stripe.pdf")
    res = client.patch(f"/api/history/{app_id}", json={"status": "interview"})
    assert res.status_code == 200
    assert res.json()["status"] == "interview"
    assert tracking.get_application_by_id(app_id)["status"] == "interview"

    # Invalid status is rejected by the model (422).
    assert client.patch(f"/api/history/{app_id}", json={"status": "bogus"}).status_code == 422
    assert client.patch("/api/history/9999", json={"status": "offered"}).status_code == 404


def test_get_stats_spans_full_history(db: Path):
    tracking.log_application("Stripe", "Senior SWE", "stripe.pdf", ats_score=90, status="interview")
    tracking.log_application("Google", "Staff Eng", "google.pdf", ats_score=80, status="offered")
    tracking.log_application("Acme", "PM", "acme.pdf", ats_score=None)  # no score -> excluded from avg

    stats = tracking.get_stats()
    assert stats["total"] == 3
    assert stats["applied"] == 1
    assert stats["interview"] == 1
    assert stats["offered"] == 1
    assert stats["avg_ats"] == 85  # (90 + 80)/2, NULL skipped


def test_stats_endpoint(client: TestClient, db: Path):
    tracking.log_application("Stripe", "Senior SWE", "stripe.pdf", ats_score=90, status="interview")
    tracking.log_application("Google", "Staff Eng", "google.pdf", ats_score=76, status="offered")

    res = client.get("/api/history/stats")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    assert body["interview"] == 1
    assert body["offered"] == 1
    assert body["avg_ats"] == 83


def test_latency_round_trips_through_history(db: Path):
    tracking.log_application(
        "Stripe", "Senior SWE", "stripe.pdf", ats_score=88,
        llm_time_ms=2900.4, compile_time_ms=220.1, total_time_ms=3120.5,
    )
    app = tracking.get_recent_applications()[0]
    assert app["llm_time_ms"] == 2900.4
    assert app["compile_time_ms"] == 220.1
    assert app["total_time_ms"] == 3120.5
    assert tracking.get_application_by_id(app["id"])["total_time_ms"] == 3120.5


def test_latency_endpoint_payload(client: TestClient, db: Path):
    tracking.log_application(
        "Stripe", "Senior SWE", "2026-10-17-1432_Stripe_Senior_SWE.pdf",
        ats_score=88, llm_time_ms=2900.4, compile_time_ms=220.1, total_time_ms=3120.5,
    )
    tracking.log_application("Acme", "PM", "acme.pdf", ats_score=70)  # timings unrecorded

    apps = client.get("/api/history?limit=5").json()["applications"]
    measured = next(a for a in apps if a["company"] == "Stripe")
    assert measured["total_time_ms"] == 3120.5
    assert measured["llm_time_ms"] == 2900.4
    assert measured["compile_time_ms"] == 220.1
    # An unmeasured run reports None, never 0 — the UI renders it as "–".
    assert next(a for a in apps if a["company"] == "Acme")["total_time_ms"] is None


def test_get_stats_averages_latency_over_measured_rows(db: Path):
    tracking.log_application(
        "Stripe", "SWE", "a.pdf", ats_score=90,
        llm_time_ms=2100.0, compile_time_ms=72.5, total_time_ms=2172.5,
    )
    tracking.log_application(
        "Google", "SWE", "b.pdf", ats_score=80,
        llm_time_ms=1800.0, compile_time_ms=65.0, total_time_ms=1865.0,
    )
    tracking.log_application("Acme", "PM", "c.pdf", ats_score=None)  # never measured

    stats = tracking.get_stats()
    assert stats["avg_ats"] == 85
    # Unmeasured rows are skipped, not counted as zero-second runs.
    assert stats["avg_total_ms"] == 2019  # (2172.5 + 1865)/2
    assert stats["avg_llm_ms"] == 1950  # (2100 + 1800)/2
    assert stats["avg_compile_ms"] == 69  # (72.5 + 65)/2, rounded


def test_get_stats_latency_is_none_before_any_measured_run(db: Path):
    tracking.log_application("Stripe", "SWE", "stripe.pdf", ats_score=88)
    stats = tracking.get_stats()
    assert stats["avg_total_ms"] is None
    assert stats["avg_llm_ms"] is None
    assert stats["avg_compile_ms"] is None


def test_init_db_backfills_columns_added_by_later_builds(db: Path):
    """A history.db written by an older build migrates in place, keeping its rows."""
    with sqlite3.connect(db) as conn:
        # The original table, before `status` and the timing columns existed.
        conn.execute(
            """
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                company TEXT NOT NULL,
                role TEXT NOT NULL,
                jd_snippet TEXT,
                pdf_filename TEXT NOT NULL,
                ats_score INTEGER,
                mode TEXT DEFAULT 'truthful',
                source TEXT DEFAULT 'cli'
            )
            """
        )
        conn.execute(
            "INSERT INTO applications (company, role, pdf_filename) VALUES (?, ?, ?)",
            ("Old Co", "SWE", "old.pdf"),
        )

    tracking.init_db()  # must migrate, not raise

    tracking.log_application("Stripe", "Senior SWE", "stripe.pdf", total_time_ms=3120.5)
    rows = {row["company"]: row for row in tracking.get_recent_applications()}
    assert set(rows) == {"Old Co", "Stripe"}
    # The migrated row keeps its data and gains sensible defaults.
    assert rows["Old Co"]["status"] == "applied"
    assert rows["Old Co"]["total_time_ms"] is None
    assert rows["Old Co"]["ats_score"] is None
    assert rows["Stripe"]["total_time_ms"] == 3120.5
