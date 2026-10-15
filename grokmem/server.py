"""Stdio MCP server. No network calls and no background work."""

from __future__ import annotations

import datetime as dt

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from grokmem import __version__
from grokmem.brand import NAME, PRODUCT, tool
from grokmem.instructions import instructions
from grokmem.journal import (
    FACT_LIMIT,
    Incoming,
    expand_summary,
    load_text,
    plan_drop,
    plan_ingest,
    prepare_rollup,
    rank,
    rollup_request,
    search_records,
    validate_save,
    derive_importance,
)
from grokmem.store import append_records, read_records

mcp = MCPServer(
    PRODUCT,
    title=NAME,
    description="Local long-term memory journal for this bot: load at task start, save durable facts as you work",
    instructions=instructions(),
    version=__version__,
)


class FactIn(BaseModel):
    """One fact to store."""

    text: str = Field(description="The fact. Longer than 400 characters is split; nothing is dropped.")
    date: str | None = Field(default=None, description="YYYY-MM-DD. Omit to use today.")
    tier: str | None = Field(default=None, description="profile, log, or note.")
    scope: str | None = Field(default=None, description="conversation, agent, or user.")
    source: str | None = Field(default=None, description="Which assistant learned it.")
    source_assistant: str | None = Field(
        default=None, description="Alias of source."
    )
    importance: str | None = Field(default=None, description="critical, normal, or low.")
    tags: list[str] | None = Field(default=None, description="Short tags, such as retracted.")


def _fail(exc: Exception) -> ToolError:
    if isinstance(exc, ToolError):
        return exc
    return ToolError(str(exc))


def _today() -> dt.date:
    return dt.date.today()


def _as_incoming(fact: FactIn | dict) -> Incoming:
    row = fact if isinstance(fact, FactIn) else FactIn.model_validate(fact)
    return Incoming(
        text=row.text,
        date=row.date,
        tier=row.tier,
        scope=row.scope,
        source=(row.source or row.source_assistant or ""),
        importance=row.importance,
        tags=row.tags,
    )


@mcp.tool(name=tool("load"))
def load_journal(page: int | None = None) -> str:
    """Read the journal. Call this first, every conversation and every resume, then follow it.

    page is 1-based, only when a previous reply says the context continues.
    Critical facts stay listed. Older weeks and months show as rollups once written.
    The reply ends with "Context loaded." or tells you to write a rollup first.
    """
    try:
        records = read_records()
        result = load_text(records, _today(), page=1 if page is None else page)
        return result.text
    except ToolError:
        raise
    except Exception as exc:
        raise _fail(exc) from exc


@mcp.tool(name=tool("save"))
def save_fact(
    text: str,
    tier: str | None = None,
    scope: str | None = None,
    importance: str | None = None,
    source: str | None = None,
    tags: list[str] | None = None,
    date: str | None = None,
) -> str:
    """Store one fact, at most 400 characters.

    tier is profile, log, or note. scope is conversation, agent, or user.
    Profile facts, text that starts with !, and a correction (Correção:,
    Correction:, or Corrected:) are stored as critical. A pending uses tier
    log. Text that starts with Pendente:, Pending:, or TODO, or a pending tag,
    is stored as normal, never low. An exact or normalized duplicate is skipped
    unless this copy is more critical. If the text is longer, use the import tool.
    """
    try:
        body = validate_save(text)
        incoming = Incoming(
            text=body,
            date=date,
            tier=tier,
            scope=scope,
            source=source,
            importance=importance,
            tags=tags,
        )
        records = read_records()
        plan = plan_ingest([incoming], records, _today())
        if not plan.records:
            if plan.empty:
                raise ValueError("Empty. A fact needs text.")
            return "Already stored. Not duplicated."
        stored = append_records(plan.records)
        saved = stored[0]
        lines = [f"Saved #{saved.id}."]
        if rank(derive_importance(body, saved.tier, importance, tags)) > 1 and saved.importance == "critical":
            lines[0] = f"Saved #{saved.id} as critical."
        request = rollup_request(read_records(), _today())
        if request:
            lines.append(request)
            lines.append("Write that rollup before the next action.")
        return "\n\n".join(lines)
    except ToolError:
        raise
    except Exception as exc:
        raise _fail(exc) from exc


@mcp.tool(name=tool("import"))
def import_facts(facts: list[FactIn]) -> str:
    """Import a batch of facts.

    Each item is text plus optional date, tier, scope, source or source_assistant,
    importance, and tags. Text over 400 characters is split without dropping
    characters. Exact and normalized duplicates are skipped. A profile fact is
    marked critical. Write any rollup this returns before the next action.
    """
    if not facts:
        raise ToolError("Pass at least one fact.")
    try:
        incoming = [_as_incoming(fact) for fact in facts]
        records = read_records()
        plan = plan_ingest(incoming, records, _today())
        if not plan.records:
            bits = ["Nothing new."]
            if plan.skipped:
                noun = "duplicate" if plan.skipped == 1 else "duplicates"
                bits.append(f"{plan.skipped} {noun} already stored.")
            if plan.empty:
                bits.append(f"{plan.empty} empty fact{'s' if plan.empty != 1 else ''} skipped.")
            return " ".join(bits)
        stored = append_records(plan.records)
        span = f"#{stored[0].id}" if len(stored) == 1 else f"#{stored[0].id}-#{stored[-1].id}"
        bits = [f"Stored {len(stored)} lines from {plan.accepted} facts ({span})."]
        if plan.split_facts:
            bits.append(
                f"Split {plan.split_facts} fact{'s' if plan.split_facts != 1 else ''} "
                f"so each line stays within {FACT_LIMIT} characters."
            )
        if plan.skipped:
            bits.append(f"Skipped {plan.skipped} duplicate{'s' if plan.skipped != 1 else ''}.")
        if plan.empty:
            bits.append(f"Skipped {plan.empty} empty fact{'s' if plan.empty != 1 else ''}.")
        request = rollup_request(read_records(), _today())
        trailer = ""
        if request:
            trailer = "\n\n" + request + "\nWrite that rollup before the next action."
        return " ".join(bits) + trailer
    except ToolError:
        raise
    except Exception as exc:
        raise _fail(exc) from exc


@mcp.tool(name=tool("rollup"))
def write_rollup(window: str, summary: str) -> str:
    """Write the next week or month summary. window looks like 2024-W01 or 2024-01.

    Put critical facts first. Keep names, ids, links, paths, decisions, and
    retractions. Invent nothing. At most 600 characters. Oldest window first.
    """
    try:
        records = read_records()
        today = _today()
        record = prepare_rollup(records, window, summary, today)
        stored = append_records([record])[0]
        label = "week" if stored.level == 1 else "month"
        lines = [f"Saved {label} rollup #{stored.id} for {stored.window}."]
        request = rollup_request(read_records(), today)
        if request:
            lines.append(request)
            lines.append("Write that rollup before the next action.")
        else:
            lines.append("No rollup is waiting.")
        return "\n\n".join(lines)
    except ToolError:
        raise
    except Exception as exc:
        raise _fail(exc) from exc


@mcp.tool(name=tool("expand"))
def expand_rollup(summary_id: int) -> str:
    """Open one week or month rollup and list what it covers, down toward the raw facts."""
    try:
        return expand_summary(read_records(), summary_id)
    except ToolError:
        raise
    except Exception as exc:
        raise _fail(exc) from exc


@mcp.tool(name=tool("find"))
def find_facts(pattern: str) -> str:
    """Search every stored fact and active rollup.

    Words match with OR, ignoring case and accents. Common words are dropped
    and hits are ranked by how many words match. The text is literal unless it
    is a valid regular expression. A week or month hit can be opened with the
    expand tool.
    """
    try:
        return search_records(read_records(), pattern)
    except ToolError:
        raise
    except Exception as exc:
        raise _fail(exc) from exc


@mcp.tool(name=tool("discard"))
def discard_rollup(summary_id: int) -> str:
    """Withdraw a bad rollup so it can be written again. Facts are not removed.

    To record a retraction, save a fact whose text starts with Retracted: and
    whose tags include retracted. Start with Resolved: when the fact is finished.
    Include the old wording so load and find can mark it.
    """
    try:
        records = read_records()
        today = _today()
        marker = plan_drop(records, summary_id, today)
        target = next(row for row in records if row.kind == "summary" and row.id == marker.target)
        append_records([marker])
        label = "week" if target.level == 1 else "month"
        return (
            f"Withdrew {label} rollup #{target.id} for {target.window}. "
            "The facts stay. It can be written again."
        )
    except ToolError:
        raise
    except Exception as exc:
        raise _fail(exc) from exc


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
