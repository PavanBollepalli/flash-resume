"""Tests for the SQLite application-history tracker and its API endpoints."""

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
    assert app["date"] == "2026-10-17"
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
