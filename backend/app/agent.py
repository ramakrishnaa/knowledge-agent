"""Multi-agent orchestration with LangGraph.

A supervisor node reads the conversation and hands the turn to one specialist
agent at a time; control returns to the supervisor after each one, until it
decides the user's request is fully answered:

    START -> supervisor -> researcher | analyst -> supervisor -> ... -> END

Each specialist runs a streaming tool-calling loop against any OpenAI-compatible
endpoint (Groq by default, OpenRouter, Gemini or a local Ollama server). Nodes push
UI events through LangGraph's stream writer and `run_turn` forwards them to the
API layer. Conversation history is kept per session by a LangGraph checkpointer.
"""

import asyncio
import json
import operator
import os
from dataclasses import dataclass
from typing import Annotated, AsyncIterator, Callable, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from openai import APIError, AsyncOpenAI, BadRequestError
from pydantic import ValidationError

from . import rag  # noqa: F401  (registers the search_knowledge_base tool)
from .telemetry import node_span
from .tools import TOOLS

BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "10"))  # tool round-trips per agent, stops runaway loops
MAX_HANDOFFS = int(os.getenv("AGENT_MAX_HANDOFFS", "4"))  # agent hand-offs per user message
FINISH = "FINISH"

# Local servers such as Ollama ignore the key, but the client requires one.
client = AsyncOpenAI(base_url=BASE_URL, api_key=os.getenv("LLM_API_KEY") or "not-needed")


@dataclass
class Agent:
    title: str
    description: str  # what the supervisor reads when choosing an agent
    tools: list[str]
    prompt: str


AGENTS: dict[str, Agent] = {
    "researcher": Agent(
        title="Search agent",
        description="Finds specific facts, figures, rules and definitions in the documents with semantic search, and cites them.",
        tools=["search_knowledge_base", "list_knowledge_base"],
        prompt="""You are the search agent. Answer from the user's knowledge base: call \
search_knowledge_base (several times with different queries if the first results are weak), \
then answer using only what the passages say. Cite sources inline as [file name]. If the \
knowledge base does not contain the answer, say so plainly instead of guessing.""",
    ),
    "analyst": Agent(
        title="Document analyst",
        description="Reads whole documents to summarise, outline, explain or compare them, and answers questions about what the knowledge base contains.",
        tools=["list_knowledge_base", "read_document"],
        prompt="""You are the document analyst. Call list_knowledge_base to see which documents \
exist, then read_document to read the relevant ones in full (page with start_chunk while \
has_more is true). Base summaries, outlines and comparisons only on what you read, and cite \
sources inline as [file name]. If the knowledge base is empty, ask the user to upload or paste \
their data first.""",
    ),
}

WORKER_SUFFIX = """

You are one agent in a team and can only call these tools: {tools}. Answer only the parts of \
the user's latest request that your tools cover. Leave the other parts out entirely, without \
apologising or saying you cannot do them: another agent will be handed them. Other agents may \
already have replied after the user's latest message; treat what they said as reliable, add \
only what is still missing, and do not repeat them. Keep answers concise and use Markdown \
where it helps."""

SUPERVISOR_PROMPT = """You are the supervisor of a team of agents. Read the conversation and \
call `route` to choose who acts next. Never answer the user yourself: your only output is a \
`route` call.

Agents:
{agents}

Pick the single agent best suited to the part of the user's latest request that is still \
unanswered. A request can need several agents in turn (for example a summary of one document, \
then a specific figure found by search). Replies are labelled with the agent that wrote them. Never send an agent \
a part it has already answered after the user's latest message. Choose FINISH once those \
replies together answer the request."""


class State(TypedDict):
    # OpenAI-format chat history; assistant messages also carry the "agent" that wrote them.
    messages: Annotated[list[dict], operator.add]
    next: str
    handoffs: int


def _condense(messages: list[dict], label: bool = False) -> list[dict]:
    """Keep user and assistant text only. Agents have different tool sets, so one agent's
    tool calls must not be replayed to another (and dropping them keeps prompts small).
    With `label`, assistant replies are prefixed with the agent that wrote them."""
    condensed = []
    for m in messages:
        if m["role"] not in ("user", "assistant") or not m.get("content"):
            continue
        content = m["content"]
        if label and m.get("agent") in AGENTS:
            content = f"[{AGENTS[m['agent']].title}] {content}"
        condensed.append({"role": m["role"], "content": content})
    return condensed


def _usage(chunk_usage) -> dict:
    return {
        "type": "usage",
        "input_tokens": chunk_usage.prompt_tokens or 0,
        "output_tokens": chunk_usage.completion_tokens or 0,
    }


# --- supervisor ---------------------------------------------------------------


async def supervisor(state: State) -> dict:
    with node_span():
        return await _supervise(state)


async def _supervise(state: State) -> dict:
    write = get_stream_writer()
    handoffs = state.get("handoffs", 0)
    if handoffs >= MAX_HANDOFFS:
        if handoffs > 0:
            write({"type": "notice", "message": f"Stopped after {MAX_HANDOFFS} agent hand-offs."})
        return {"next": FINISH}

    # FINISH is only offered once some agent has replied to this message.
    choices = list(AGENTS) + ([FINISH] if handoffs else [])
    route = {
        "type": "function",
        "function": {
            "name": "route",
            "description": "Choose the next agent, or FINISH when the request is fully answered.",
            "parameters": {
                "type": "object",
                "properties": {
                    "next": {"type": "string", "enum": choices},
                    "reason": {"type": "string", "description": "One short sentence explaining the choice"},
                },
                "required": ["next", "reason"],
            },
        },
    }
    agents = "\n".join(f"- {name}: {a.description}" for name, a in AGENTS.items())
    decision: dict = {}
    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SUPERVISOR_PROMPT.format(agents=agents)}, *_condense(state["messages"], label=True)],
            tools=[route],
            tool_choice={"type": "function", "function": {"name": "route"}},
        )
        if response.usage:
            write(_usage(response.usage))
        if calls := response.choices[0].message.tool_calls:
            decision = json.loads(calls[0].function.arguments or "{}")
    except json.JSONDecodeError:
        pass
    except BadRequestError as exc:
        # The model sometimes answers in prose instead of calling `route`; use the fallback below.
        if exc.code != "tool_use_failed":
            raise
    nxt = decision.get("next")
    if nxt not in choices:  # unparseable routing: answer with the generalist, or stop
        nxt = "researcher" if not handoffs else FINISH
    if nxt != FINISH:
        write({"type": "handoff", "agent": nxt, "title": AGENTS[nxt].title, "reason": decision.get("reason", "")})
    return {"next": nxt, "handoffs": handoffs + 1}


# --- specialist agents ----------------------------------------------------------


async def _execute(call: dict) -> tuple[str, bool]:
    """Run one tool call. Returns (output, is_error)."""
    tool = TOOLS.get(call["name"])
    if tool is None:
        return f"Unknown tool '{call['name']}'", True
    try:
        args = json.loads(call["arguments"] or "{}")
    except json.JSONDecodeError as exc:
        return f"Arguments were not valid JSON: {exc}", True
    try:
        return await asyncio.to_thread(tool.run, args), False
    except ValidationError as exc:
        return f"Invalid input: {exc}", True
    except Exception as exc:  # report tool failures to the model instead of crashing the loop
        return f"Error: {exc}", True


async def _run_agent(agent: Agent, history: list[dict], new: list[dict]) -> AsyncIterator[dict]:
    """Stream the agent's tool-calling loop, appending each completed turn to `new`."""
    system = {"role": "system", "content": agent.prompt + WORKER_SUFFIX.format(tools=", ".join(agent.tools))}
    tools = [TOOLS[name].definition() for name in agent.tools]

    for _ in range(MAX_STEPS):
        stream = await client.chat.completions.create(
            model=MODEL,
            messages=[system, *_condense(history), *new],
            tools=tools,
            stream=True,
            stream_options={"include_usage": True},
        )

        text = ""
        calls: dict[int, dict] = {}  # tool calls arrive as fragments keyed by index
        try:
            async for chunk in stream:
                if chunk.usage:
                    yield _usage(chunk.usage)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if reasoning := getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None):
                    yield {"type": "thinking", "text": reasoning}
                if delta.content:
                    text += delta.content
                    yield {"type": "text", "text": delta.content}
                for fragment in delta.tool_calls or []:
                    call = calls.setdefault(fragment.index, {"id": "", "name": "", "arguments": ""})
                    if fragment.id:
                        call["id"] = fragment.id
                    if fragment.function and fragment.function.name:
                        call["name"] += fragment.function.name
                        yield {"type": "tool_start", "id": call["id"], "name": call["name"]}
                    if fragment.function and fragment.function.arguments:
                        call["arguments"] += fragment.function.arguments
        except APIError as exc:
            # Some providers (Groq) reject calls to tools outside this agent's set mid-stream.
            # Hand the turn back to the supervisor, which can route to the agent that has the tool.
            if "tool call validation failed" not in str(exc).lower():
                raise
            for c in calls.values():
                yield {"type": "tool_result", "id": c["id"], "output": "Not available to this agent", "is_error": True}
            yield {"type": "notice", "message": f"{agent.title} needs another agent's tool; handing back to the supervisor."}
            if text:
                new.append({"role": "assistant", "content": text})
            return

        tool_calls = [calls[i] for i in sorted(calls)]
        assistant = {"role": "assistant", "content": text or None}
        if tool_calls:
            assistant["tool_calls"] = [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                for c in tool_calls
            ]
        new.append(assistant)

        if not tool_calls:
            return

        for c in tool_calls:
            try:
                parsed = json.loads(c["arguments"] or "{}")
            except json.JSONDecodeError:
                parsed = c["arguments"]
            yield {"type": "tool_call", "id": c["id"], "name": c["name"], "input": parsed}

        results = await asyncio.gather(*(_execute(c) for c in tool_calls))
        for c, (output, is_error) in zip(tool_calls, results):
            yield {"type": "tool_result", "id": c["id"], "output": output, "is_error": is_error}
            new.append({"role": "tool", "tool_call_id": c["id"], "content": output})

    yield {"type": "notice", "message": f"{agent.title} stopped after {MAX_STEPS} tool steps."}


def _agent_node(name: str, agent: Agent) -> Callable:
    async def node(state: State) -> dict:
        write = get_stream_writer()
        new: list[dict] = []
        with node_span():
            async for event in _run_agent(agent, state["messages"], new):
                write(event)
        # Tagged only here: providers reject unknown message fields, and _condense drops the tag.
        return {"messages": [{**m, "agent": name} if m["role"] == "assistant" else m for m in new]}

    return node


# --- graph ----------------------------------------------------------------------

checkpointer = InMemorySaver()  # per-session history; restarting the server clears it

def route_next(state: State) -> str:
    return state["next"]


_builder = StateGraph(State)
_builder.add_node("supervisor", supervisor)
for _name, _agent in AGENTS.items():
    _builder.add_node(_name, _agent_node(_name, _agent))
    _builder.add_edge(_name, "supervisor")
_builder.add_edge(START, "supervisor")
_builder.add_conditional_edges("supervisor", route_next, {**{n: n for n in AGENTS}, FINISH: END})
graph = _builder.compile(checkpointer=checkpointer)


async def run_turn(session_id: str, message: str) -> AsyncIterator[dict]:
    """Run the graph for one user message, yielding UI events and a final `done`."""
    usage = {"input_tokens": 0, "output_tokens": 0}
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 4 * MAX_HANDOFFS + 4}
    turn = {"messages": [{"role": "user", "content": message}], "next": "", "handoffs": 0}

    async for event in graph.astream(turn, config, stream_mode="custom"):
        if event["type"] == "usage":
            usage["input_tokens"] += event["input_tokens"]
            usage["output_tokens"] += event["output_tokens"]
        else:
            yield event
    yield {"type": "done", "usage": usage}


async def session_exists(session_id: str) -> bool:
    return await checkpointer.aget_tuple({"configurable": {"thread_id": session_id}}) is not None


async def delete_session(session_id: str) -> None:
    await checkpointer.adelete_thread(session_id)
