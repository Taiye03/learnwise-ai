from fastapi.testclient import TestClient

import api

client = TestClient(api.app)


def setup_function():
    api.sessions.clear()
    api.safety_alerts.clear()


def test_full_conversation():
    start = client.post("/api/session").json()
    assert "AI tutor" in start["greeting"]
    sid = start["session_id"]
    reply = client.post("/api/tutor/message", json={"session_id": sid, "message": "hint"}).json()
    assert reply["rung"] == 1 and reply["ai_label"] == "AI Tutor"
    reply = client.post("/api/tutor/message", json={"session_id": sid, "message": "5/6"}).json()
    assert reply["status"] == "solved"


def test_unknown_session():
    r = client.post("/api/tutor/message", json={"session_id": "nope", "message": "hint"})
    assert r.status_code == 404


def test_teacher_view_hidden_below_five():
    client.post("/api/session")
    assert client.get("/api/teacher/summary").json()["hidden"] is True


def test_teacher_view_after_seed():
    client.post("/api/demo/seed")
    summary = client.get("/api/teacher/summary").json()
    assert summary["hidden"] is False
    assert summary["learners"] == 6
    assert summary["solved"] == 3
    assert summary["need_teacher"] == 2
    assert "message" not in str(summary)


def test_safety_alert_has_no_text():
    sid = client.post("/api/session").json()["session_id"]
    client.post("/api/tutor/message", json={"session_id": sid, "message": "someone is hurting me"})
    alerts = client.get("/api/safeguarding/alerts").json()["alerts"]
    assert len(alerts) == 1 and "hurting" not in str(alerts)
