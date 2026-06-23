import asyncio
import json
import logging
from typing import AsyncGenerator
from .llm_client import call_llm
from .system_prompt import SAP_SYSTEM_PROMPT
from . import memory
from ..tools.registry import registry

logger = logging.getLogger(__name__)

# Lowered from 5: the 5 SD tools rarely need more than 2-3 chained calls
# (customer -> orders -> delivery/billing). A lower cap reduces worst-case
# latency and OpenAI spend on runaway loops without limiting real use cases.
MAX_TOOL_ITERATIONS = 3


def _sse(payload: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(payload)}\n\n"


async def _execute_tool_call(tc) -> dict:
    """
    Execute a single tool call and return a uniform result dict.
    Never raises — all failure modes are converted into an {"error": ...}
    payload so a single bad tool call can't take down the whole turn.
    """
    tool_name = tc.function.name
    try:
        tool_args = json.loads(tc.function.arguments)
    except json.JSONDecodeError:
        tool_args = {}

    try:
        tool = registry.get(tool_name)
        result = await tool.execute(**tool_args)
    except KeyError as e:
        result = {"error": str(e)}
    except TypeError as e:
        result = {"error": f"Invalid arguments for {tool_name}: {e}"}
    except Exception as e:
        logger.exception("Tool '%s' failed", tool_name)
        result = {"error": f"Tool '{tool_name}' failed unexpectedly."}

    return {"tool_call_id": tc.id, "tool_name": tool_name, "tool_args": tool_args, "result": result}


async def run_agent(user_message: str, history: list, session_id: str) -> AsyncGenerator[str, None]:
    """
    Core agentic loop with SSE streaming and HANA-backed memory.

    Flow:
      0. Load prior turns from HANA (via CAP) for this session_id, falling
         back to client-supplied history if CAP/HANA is unreachable
      1. Build messages array (system + history + user)
      2. Call LLM with tool schemas (non-streaming, so we can inspect tool_calls)
      3. If tool_calls -> execute tools CONCURRENTLY -> inject results -> loop again
      4. If no tool_calls -> stream the SAME completion's content (no second call)
      5. If MAX_TOOL_ITERATIONS is hit without resolution, tell the user plainly
      6. Persist the user turn and the final assistant turn back to HANA

    Memory persistence is best-effort: if CAP/HANA is unreachable, the
    agent still answers using whatever history the client sent — it just
    won't have durable memory across page reloads.

    Sequence numbers for persisted messages are assigned by CAP/HANA
    (see memory.save_message), not computed here, to avoid races when
    multiple requests for the same session arrive concurrently.
    """
    tools = registry.all_schemas()

    persisted_history = await memory.load_history(session_id)
    effective_history = persisted_history if persisted_history else history

    messages = (
        [{"role": "system", "content": SAP_SYSTEM_PROMPT}]
        + effective_history
        + [{"role": "user", "content": user_message}]
    )

    # Best-effort persistence of the user's turn. CAP assigns the
    # sequence number atomically, so concurrent requests for the same
    # session can't collide on next_seq the way a client-computed
    # counter could.
    cap_session_key = None
    try:
        cap_session_key = await memory.ensure_session(session_id)
        await memory.save_message(cap_session_key, "user", user_message)
    except Exception:
        # exc_info=True surfaces the actual underlying error (e.g. a
        # 4xx/5xx from CAP, a connection failure, a bad response shape)
        # instead of only this generic message — without it, "message
        # never gets persisted" produces no diagnosable signal anywhere,
        # which is exactly what made this bug invisible until now.
        logger.warning("Could not persist user turn for session %s (CAP/HANA unreachable?)", session_id, exc_info=True)

    tool_calls_log = []
    final_answer_text = ""

    try:
        resolved = False

        for iteration in range(MAX_TOOL_ITERATIONS):
            response = await call_llm(messages=messages, tools=tools, stream=False)

            choice = response.choices[0]
            msg = choice.message
            finish_reason = choice.finish_reason

            # ── No tool calls requested: this IS the final answer ──────
            if finish_reason != "tool_calls" and not msg.tool_calls:
                content = msg.content or ""
                final_answer_text = content
                chunk_size = 6
                for i in range(0, len(content), chunk_size):
                    piece = content[i : i + chunk_size]
                    yield _sse({"type": "token", "content": piece, "session_id": session_id})

                resolved = True
                break

            # ── Tool calls requested ────────────────────────────────────
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )

            # Announce all tool starts up front, then execute concurrently.
            # Previously these ran sequentially (one CAP round-trip after
            # another); for a turn requesting 2-3 tools, that serialized
            # latency adds up. asyncio.gather runs them in parallel since
            # each is an independent HTTP call to CAP.
            for tc in msg.tool_calls:
                try:
                    args_preview = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args_preview = {}
                yield _sse({"type": "tool_start", "tool": tc.function.name, "args": args_preview})

            tool_results = await asyncio.gather(*(_execute_tool_call(tc) for tc in msg.tool_calls))

            for tr in tool_results:
                tool_calls_log.append(
                    {"tool": tr["tool_name"], "args": tr["tool_args"], "result": tr["result"]}
                )
                yield _sse({"type": "tool_end", "tool": tr["tool_name"], "result": tr["result"]})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tr["tool_call_id"],
                        "content": json.dumps(tr["result"]),
                    }
                )

        if not resolved:
            # Hit MAX_TOOL_ITERATIONS without a final answer.
            # Make ONE more call without tools to force a wrap-up,
            # rather than silently dropping the turn.
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Please summarize what you found so far in plain business "
                        "language. Do not call any more tools."
                    ),
                }
            )
            wrapup = await call_llm(messages=messages, tools=None, stream=False)
            content = wrapup.choices[0].message.content or (
                "I wasn't able to fully resolve this within the tool-call limit. "
                "Could you narrow the request (e.g. a specific customer or order)?"
            )
            final_answer_text = content
            chunk_size = 6
            for i in range(0, len(content), chunk_size):
                yield _sse({"type": "token", "content": content[i : i + chunk_size], "session_id": session_id})

        # Best-effort persistence of the assistant's final turn.
        # This MUST happen before the 'done' event is yielded below, not
        # after. run_agent is an async generator feeding a StreamingResponse;
        # once 'done' is yielded, the frontend has everything it needs and
        # stops reading the stream. If the underlying connection gets torn
        # down at that point (browser, proxy, or Gorouter closing an
        # apparently-finished response), an async generator's code AFTER
        # its final yield is not guaranteed to run to completion — so a
        # save placed after 'done' races the teardown and can simply never
        # execute, with no exception raised anywhere to explain why.
        if cap_session_key and final_answer_text:
            try:
                await memory.save_message(cap_session_key, "assistant", final_answer_text, tool_calls_log)
            except Exception:
                logger.warning("Could not persist assistant turn for session %s", session_id, exc_info=True)

        yield _sse({"type": "done", "session_id": session_id, "tool_calls": tool_calls_log})

    except Exception:
        # Never leak internal exception text (stack details, internal
        # hostnames, etc.) to the client. Log full detail server-side only.
        logger.exception("Agent loop failed for session %s", session_id)
        yield _sse({"type": "error", "message": "The agent encountered an error processing your request. Please try again."})