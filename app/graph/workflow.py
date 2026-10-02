from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    accommodation_agent,
    blocked_node,
    finish_node,
    initialize_graph,
    restaurant_agent,
    route_step,
    supervisor,
    temple_agent,
    travel_agent,
    unknown_agent,
)
from app.graph.state import GraphState


def build_workflow():

    graph = StateGraph(GraphState)

    graph.add_node(
        "initialize",
        initialize_graph,
    )

    graph.add_node(
        "supervisor",
        supervisor,
    )

    graph.add_node(
        "temple",
        temple_agent,
    )

    graph.add_node(
        "accommodation",
        accommodation_agent,
    )

    graph.add_node(
        "restaurant",
        restaurant_agent,
    )

    graph.add_node(
        "travel",
        travel_agent,
    )

    graph.add_node(
        "unknown",
        unknown_agent,
    )

    graph.add_node(
        "blocked",
        blocked_node,
    )

    graph.add_node(
        "finish",
        finish_node,
    )

    graph.add_edge(
        START,
        "initialize",
    )

    graph.add_edge(
        "initialize",
        "supervisor",
    )

    graph.add_conditional_edges(
        "supervisor",
        route_step,
        {
            "temple": "temple",
            "accommodation": "accommodation",
            "restaurant": "restaurant",
            "travel": "travel",
            "unknown": "unknown",
            "blocked": "blocked",
            "finish": "finish",
        },
    )

    for agent in [
        "temple",
        "accommodation",
        "restaurant",
        "travel",
    ]:
        graph.add_edge(
            agent,
            "supervisor",
        )

    graph.add_edge(
        "unknown",
        END,
    )

    graph.add_edge(
        "blocked",
        END,
    )

    graph.add_edge(
        "finish",
        END,
    )

    return graph.compile()


workflow = build_workflow()