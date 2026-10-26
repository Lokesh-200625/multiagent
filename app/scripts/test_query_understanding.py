from app.services.intent import (
    understand_query,
)


def run(
    message: str,
):
    print()
    print("=" * 80)
    print(message)
    print("=" * 80)

    result = understand_query(
        message=message,
        conversation_state={},
    )

    print(
        result.model_dump_json(
            indent=2,
            ensure_ascii=False,
        )
    )


def main():

    run(
        "మేము రేపు యాదాద్రి వెళ్ళాలి. "
        "ఉదయం దర్శనం కోసం ఎప్పుడు బయలుదేరాలి? "
        "హోటల్ కూడా కావాలి."
    )

    run(
        "repu family tho Yadagirigutta velthunnam. "
        "Hyderabad nunchi morning start avvali, "
        "temple daggara hotel kavali, "
        "hotel daggara manchi restaurant kuda kavali."
    )

    run(
        "We are visiting Yadadri next Saturday. "
        "Find a hotel near the temple for one night "
        "and a restaurant near the hotel."
    )

    run(
        "Hotel Ashoka near Thousand Pillar Temple kavali."
    )


if __name__ == "__main__":
    main()