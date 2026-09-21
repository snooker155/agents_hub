"""Report a LangGraph graph's runs to an Agents Hub, without changing the graph.

    from agents_hub_langgraph import HubTracer

    tracer = HubTracer(url="https://hub.internal", token="ahc_…")
    tracer.report_graph(graph)          # once, so the hub can draw it
    graph.invoke(state, config={"callbacks": [tracer]})

The graph keeps its own models, tools, prompts, deployment and triggers. This
package only watches, and it is built so that watching can never be the reason a
production run fails: every callback swallows its errors, all HTTP happens on a
worker thread, and the queue is bounded.

Installed beside a graph rather than inside this hub, which is why it lives in
``clients/`` with its own pyproject: nobody is going to pip-install a whole
multi-agent platform into their production image to get a dashboard.
"""
from agents_hub_langgraph.client import HubClient, Transport
from agents_hub_langgraph.topology import graph_topology
from agents_hub_langgraph.tracer import HubTracer

__version__ = "0.1.0"

__all__ = ["HubTracer", "HubClient", "Transport", "graph_topology", "__version__"]
