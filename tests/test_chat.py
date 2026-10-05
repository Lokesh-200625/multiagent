from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.plan import ExecutionPlan
from app.models.understanding import QueryUnderstanding


client = TestClient(app)


def make_understanding(
    intent: str = "search",
    language: str = "en",
) -> QueryUnderstanding:
    return QueryUnderstanding(
        intent=intent,
        confidence=0.95,
        language=language,
        requires_clarification=False,
        clarification_question=None,
        entities=[],
        constraints=[],
        preferences=[],
        dates_times=[],
        locations=[],
        group=None,
        required_capabilities=[],
        subqueries=[],
        dependencies=[],
    )


def test_empty_message_returns_422():
    response = client.post(
        "/chat",
        json={"message": ""},
    )

    assert response.status_code == 422


def test_emergency_bypasses_llm():
    with patch(
        "app.api.chat.understand_query"
    ) as mock_llm:
        response = client.post(
            "/chat",
            json={
                "message": (
                    "Someone is unconscious "
                    "and not breathing."
                ),
            },
        )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "EMERGENCY"
    assert data["intent"] == "emergency"
    assert data["reply"]

    mock_llm.assert_not_called()


def test_normal_chat_returns_slim_response():
    understanding = make_understanding()

    with patch(
        "app.api.chat.understand_query",
        return_value=understanding,
    ):
        response = client.post(
            "/chat",
            json={
                "message": "Hello",
            },
        )

    assert response.status_code == 200

    data = response.json()

    assert set(data.keys()) == {
        "session_id",
        "intent",
        "reply",
        "clarification",
        "status",
    }

    assert data["intent"] == "search"
    assert data["reply"]


def test_stub_status_is_preserved():
    understanding = make_understanding()

    fake_graph_result = {
        "status": "STUB",
        "step_results": {
            "q1": {
                "agent": "temple",
                "status": "STUB",
                "stub": True,
            }
        },
        "execution_plan": {
            "intent": "search",
            "steps": [
                {
                    "step_id": "q1",
                    "domain": "temple",
                    "intent": "search",
                    "question": "Find a temple.",
                }
            ],
        },
    }

    with patch(
        "app.api.chat.understand_query",
        return_value=understanding,
    ), patch(
        "app.api.chat.workflow.invoke",
        return_value=fake_graph_result,
    ):
        response = client.post(
            "/chat",
            json={
                "message": "Find a temple.",
            },
        )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "STUB"


def test_clarification_is_returned():
    understanding = make_understanding()

    fake_plan = ExecutionPlan(
        intent="search",
        steps=[],
        requires_clarification=True,
        clarification_question=(
            "Which city are you travelling from?"
        ),
        status="READY",
    )

    with patch(
        "app.api.chat.understand_query",
        return_value=understanding,
    ), patch(
        "app.api.chat.build_execution_plan",
        return_value=fake_plan,
    ), patch(
        "app.api.chat.validate_execution_plan",
        return_value=fake_plan.model_dump(),
    ):
        response = client.post(
            "/chat",
            json={
                "message": "Plan my pilgrimage.",
            },
        )

    assert response.status_code == 200

    data = response.json()

    assert data["clarification"] == (
        "Which city are you travelling from?"
    )


def test_llm_unavailable_returns_503():
    with patch(
        "app.api.chat.understand_query",
        side_effect=RuntimeError(
            "LLM_UNAVAILABLE: all configured Groq keys failed."
        ),
    ):
        response = client.post(
            "/chat",
            json={
                "message": "Find temples in Hyderabad.",
            },
        )

    assert response.status_code == 503

    assert (
        response.json()["detail"]
        == "Query understanding service is temporarily unavailable."
    )


def test_chat_survives_redis_unavailable():
    understanding = make_understanding()

    with patch(
        "app.api.chat.get_session",
        return_value=None,
    ), patch(
        "app.api.chat.save_session",
        return_value=False,
    ), patch(
        "app.api.chat.understand_query",
        return_value=understanding,
    ):
        response = client.post(
            "/chat",
            json={
                "message": "Hello",
            },
        )

    assert response.status_code == 200

    data = response.json()

    assert data["session_id"]
    assert data["intent"] == "search"
    assert data["reply"]


def test_redis_failure_does_not_break_emergency():
    with patch(
        "app.api.chat.get_session",
        return_value=None,
    ), patch(
        "app.api.chat.save_session",
        return_value=False,
    ), patch(
        "app.api.chat.understand_query"
    ) as mock_llm:
        response = client.post(
            "/chat",
            json={
                "message": (
                    "Someone is unconscious "
                    "and not breathing."
                ),
            },
        )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "EMERGENCY"
    assert data["intent"] == "emergency"

    mock_llm.assert_not_called()