from fastapi.testclient import TestClient
import os

from main import app


client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "healthy"


def test_create_film_validation():
    # Missing required fields
    r = client.post("/api/v1/films", json={})
    assert r.status_code == 422 or r.status_code == 400


def test_create_and_status_cycle():
    payload = {
        "topic": "Old Dhaka vs New Dhaka — a breathtaking 60-second cinematic documentary trailer",
        "duration_seconds": 60,
        "genre": "cinematic documentary",
        "language": "English",
        "aspect_ratio": "16:9"
    }
    r = client.post("/api/v1/films", json=payload)
    assert r.status_code in (200, 201)
    data = r.json()
    assert "project_id" in data
    project_id = data["project_id"]

    # Check status endpoint
    r2 = client.get(f"/api/v1/films/{project_id}")
    assert r2.status_code == 200
    s = r2.json()
    assert s["project_id"] == project_id
    assert "status" in s
