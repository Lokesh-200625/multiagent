from app.services.intent import (
    understand_query,
)
from app.services.query_planner import (
    build_execution_plan,
)
from app.services.query_resolution import (
    resolve_understanding,
)


def run(message: str):

    print()
    print("=" * 80)
    print(message)
    print("=" * 80)

    understanding = understand_query(
        message=message,
        conversation_state={},
    )

    resolved = resolve_understanding(
        understanding
    )

    plan = build_execution_plan(
        resolved
    )

    print(
        plan.model_dump_json(
            indent=2,
            ensure_ascii=False,
        )
    )


def main():

    run(
        "Hotel Ashoka near Thousand Pillar Temple kavali."
    )

    run(
        "We are visiting Yadadri next Saturday. "
        "Find a hotel near the temple for one night "
        "and a restaurant near the hotel."
    )

    run(
        "మేము రేపు యాదాద్రి వెళ్ళాలి. "
        "ఉదయం దర్శనం కోసం ఎప్పుడు బయలుదేరాలి? "
        "హోటల్ కూడా కావాలి."
    )


if __name__ == "__main__":
    main()