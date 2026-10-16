from app.graph.nodes import _complete_stub_step, supervisor


def test_stub_agent_marks_step_complete_for_supervisor():
    state = {
        "session_id": "regression",
        "execution_plan": {
            "steps": [
                {
                    "step_id": "q1",
                    "domain": "accommodation",
                    "status": "READY",
                    "depends_on": [],
                }
            ]
        },
        "current_step_id": "q1",
        "current_step": {
            "step_id": "q1",
            "domain": "accommodation",
        },
        "step_results": {},
        "status": "RUNNING",
    }

    completed = _complete_stub_step(state, "accommodation")

    assert completed["status"] == "RUNNING"
    assert completed["step_results"]["q1"]["status"] == "COMPLETED"
    assert completed["step_results"]["q1"]["stub"] is True

    next_state = supervisor(completed)

    assert next_state["status"] == "COMPLETED"
    assert next_state["current_step_id"] is None
