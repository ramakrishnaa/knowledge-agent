"""Optional tracing to Arize AX or Arize Phoenix via OpenTelemetry + OpenInference.

Set ARIZE_SPACE_ID and ARIZE_API_KEY to send traces to Arize AX (cloud), or
PHOENIX_COLLECTOR_ENDPOINT to send them to Phoenix (local or Phoenix Cloud,
with PHOENIX_API_KEY). With neither set, tracing stays off.

Each chat turn becomes one trace: the LangGraph run, a span per agent node,
every LLM call (prompt, completion, token counts) and every knowledge-base
retrieval with the passages it returned.
"""

import logging
import os
from contextlib import contextmanager

from openinference.instrumentation.langchain import LangChainInstrumentor, get_current_span
from openinference.instrumentation.openai import OpenAIInstrumentor
from opentelemetry import trace

log = logging.getLogger(__name__)
PROJECT_NAME = os.getenv("TRACING_PROJECT_NAME", "knowledge-agent")
_enabled = False


@contextmanager
def node_span():
    """Make the running LangGraph node's span the active OpenTelemetry span, so the LLM
    calls and retrievals inside the node nest under it instead of under the request."""
    span = get_current_span() if _enabled else None
    if span is None:
        yield
        return
    with trace.use_span(span, end_on_exit=False):
        yield


def setup_tracing() -> str | None:
    """Register a tracer provider and instrument LangGraph and OpenAI. Returns the backend name."""
    if os.getenv("ARIZE_SPACE_ID") and os.getenv("ARIZE_API_KEY"):
        from arize.otel import register

        provider = register(
            space_id=os.environ["ARIZE_SPACE_ID"],
            api_key=os.environ["ARIZE_API_KEY"],
            project_name=PROJECT_NAME,
            verbose=False,
        )
        target = "Arize AX"
    elif os.getenv("PHOENIX_COLLECTOR_ENDPOINT"):
        from phoenix.otel import register

        # Reads PHOENIX_COLLECTOR_ENDPOINT and PHOENIX_API_KEY from the environment.
        provider = register(project_name=PROJECT_NAME, batch=True, verbose=False)
        target = "Phoenix"
    else:
        return None

    global _enabled
    LangChainInstrumentor().instrument(tracer_provider=provider)
    OpenAIInstrumentor().instrument(tracer_provider=provider)
    _enabled = True
    log.info("Tracing to %s (project '%s')", target, PROJECT_NAME)
    return target
