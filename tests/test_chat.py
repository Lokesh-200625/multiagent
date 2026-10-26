from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.brain import BrainResult, BrainSubquery
from app.services.llm import LLMUnavailable

client = TestClient(app)


def _brain_greeting() -> BrainResult:
    return BrainResult(
        language="en",
        category="greeting",
        rewritten_query="hi",
        direct_reply="Namaste! Welcome to PilgrimAI.",
        subqueries=[],
    )


def _brain_temple() -> BrainResult:
    return BrainResult(
        language="en",
        category="temple_info",
        rewritten_query="What are the Yadadri temple timings?",
        direct_reply=None,
        subqueries=[
            BrainSubquery(
                id="q1",
                category="temple_info",
                question="What are the Yadadri temple timings?",
                entities=[
                    {"mention": "Yadadri", "entity_type": "temple"}
                ],
            )
        ],
    )


def _temple_results() -> list[dict]:
    return [
        {
            "step_id": "q1",
            "category": "temple_info",
            "question": "What are the Yadadri temple timings?",
            "status": "COMPLETED",
            "error": None,
            "evidence": [
                {
                    "source": "temple_rag",
                    "kind": "chunk",
                    "name": "Yadadri",
                    "text": "Darshan timings are 6:30 AM to 1:00 PM and 3:00 PM to 8:30 PM.",
                }
            ],
        }
    ]


def test_empty_message_returns_422():
    response = client.post("/chat", json={"message": ""})
    assert response.status_code == 422


def test_whitespace_message_returns_400():
    response = client.post("/chat", json={"message": "   "})
    assert response.status_code == 400


def test_emergency_bypasses_brain():
    with patch("app.api.chat.understand") as mock_brain:
        response = client.post(
            "/chat",
            json={"message": "Someone is unconscious and not breathing."},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "EMERGENCY"
    assert data["category"] == "emergency"
    assert data["reply"]
    mock_brain.assert_not_called()


def test_greeting_answered_directly():
    with patch(
        "app.api.chat.understand",
        return_value=_brain_greeting(),
    ), patch("app.api.chat.get_session", return_value=None), patch(
        "app.api.chat.save_session", return_value=True
    ):
        response = client.post("/chat", json={"message": "hiiii helloww"})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "DIRECT"
    assert data["category"] == "greeting"
    assert "Namaste" in data["reply"]


def test_llm_unavailable_returns_503():
    with patch(
        "app.api.chat.understand",
        side_effect=LLMUnavailable("all keys failed"),
    ), patch("app.api.chat.get_session", return_value=None), patch(
        "app.api.chat.save_session", return_value=True
    ):
        response = client.post("/chat", json={"message": "yadadri timings"})

    assert response.status_code == 503
    assert "temporarily unavailable" in response.json()["detail"]


def test_temple_flow_composes_grounded_reply():
    results = _temple_results()

    with patch(
        "app.api.chat.understand",
        return_value=_brain_temple(),
    ), patch(
        "app.api.chat.execute",
        return_value=results,
    ), patch(
        "app.api.chat.compose_answer",
        return_value="Darshan runs 6:30 AM to 1:00 PM and 3:00 PM to 8:30 PM as per temple records.",
    ), patch("app.api.chat.get_session", return_value=None), patch(
        "app.api.chat.save_session", return_value=True
    ):
        response = client.post(
            "/chat",
            json={"message": "yadadri temple timings?"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "COMPLETED"
    assert data["category"] == "temple_info"
    assert "6:30 AM" in data["reply"]
    assert data["evidence"]


def test_hallucinated_numbers_trigger_degraded_render():
    results = _temple_results()

    with patch(
        "app.api.chat.understand",
        return_value=_brain_temple(),
    ), patch(
        "app.api.chat.execute",
        return_value=results,
    ), patch(
        "app.api.chat.compose_answer",
        return_value="It costs 99999 rupees and opens at 4:45 AM.",
    ), patch("app.api.chat.get_session", return_value=None), patch(
        "app.api.chat.save_session", return_value=True
    ):
        response = client.post(
            "/chat",
            json={"message": "yadadri temple timings?"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "DEGRADED"
    assert "6:30 AM" in data["reply"]


def test_session_history_persisted():
    saved: dict = {}

    def fake_save(session_id, state):
        saved.update(state)
        return True

    with patch(
        "app.api.chat.understand",
        return_value=_brain_greeting(),
    ), patch("app.api.chat.get_session", return_value=None), patch(
        "app.api.chat.save_session", side_effect=fake_save
    ):
        client.post("/chat", json={"message": "hello"})

    assert saved["history"]
    assert saved["history"][-1]["category"] == "greeting"


def test_reply_never_contains_json_meta():
    with patch(
        "app.api.chat.understand",
        return_value=_brain_greeting(),
    ), patch("app.api.chat.get_session", return_value=None), patch(
        "app.api.chat.save_session", return_value=True
    ):
        response = client.post("/chat", json={"message": "namaste"})

    reply = response.json()["reply"]
    assert "evidence" not in reply
    assert "subqueries" not in reply
