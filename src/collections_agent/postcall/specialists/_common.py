"""Shared transcript formatting for the four specialists — every specialist sees the same
plain-text rendering of the call, so this is the one place that decides what "the transcript"
means to an LLM.
"""

from __future__ import annotations

from collections_agent.postcall.transcript import Transcript


def format_transcript_for_llm(transcript: Transcript) -> str:
    lines = [f"{turn.role}: {turn.content}" for turn in transcript.turns]
    lines.append("")
    lines.append("Tool calls made during the call:")
    if transcript.tool_calls:
        for call in transcript.tool_calls:
            lines.append(f"- {call.name}({call.arguments}) -> {call.result}")
    else:
        lines.append("(none)")
    return "\n".join(lines)
