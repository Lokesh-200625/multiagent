from app.services.entity_registry import (
    get_entity_registry,
)


def print_result(
    title: str,
    result: dict,
):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)
    print(result)


def main():

    registry = get_entity_registry()

    # ---------------------------------------------------------
    # Same hotel name, temple context resolves it
    # ---------------------------------------------------------

    result = registry.resolve(
        mention="Hotel Ashoka",
        entity_type="accommodation",
        context={
            "temple_id": "T0012",
        },
    )

    print_result(
        "Hotel Ashoka + T0012",
        result,
    )

    # ---------------------------------------------------------
    # Same hotel name, different temple
    # ---------------------------------------------------------

    result = registry.resolve(
        mention="Hotel Ashoka",
        entity_type="accommodation",
        context={
            "temple_id": "T0013",
        },
    )

    print_result(
        "Hotel Ashoka + T0013",
        result,
    )

    # ---------------------------------------------------------
    # Same hotel, no context => ambiguous
    # ---------------------------------------------------------

    result = registry.resolve(
        mention="Hotel Ashoka",
        entity_type="accommodation",
    )

    print_result(
        "Hotel Ashoka without context",
        result,
    )

    # ---------------------------------------------------------
    # Exact temple alias
    # ---------------------------------------------------------

    result = registry.resolve(
        mention="Yadagirigutta",
        entity_type="temple",
    )

    print_result(
        "Yadagirigutta",
        result,
    )

    # ---------------------------------------------------------
    # Spelling variation
    # ---------------------------------------------------------

    result = registry.resolve(
        mention="Yadagiri Gutta",
        entity_type="temple",
    )

    print_result(
        "Yadagiri Gutta",
        result,
    )

    # ---------------------------------------------------------
    # Genuine ambiguous alias
    # ---------------------------------------------------------

    result = registry.resolve(
        mention="Hanamkonda",
        entity_type="temple",
    )

    print_result(
        "Hanamkonda",
        result,
    )

    # ---------------------------------------------------------
    # Same restaurant name + temple context
    # ---------------------------------------------------------

    result = registry.resolve(
        mention="Flavor's Inn",
        entity_type="restaurant",
        context={
            "temple_id": "T0001",
        },
    )

    print_result(
        "Flavor's Inn + T0001",
        result,
    )

    result = registry.resolve(
        mention="Flavor's Inn",
        entity_type="restaurant",
        context={
            "temple_id": "T0022",
        },
    )

    print_result(
        "Flavor's Inn + T0022",
        result,
    )


if __name__ == "__main__":
    main()