"""Stdio session against the grokmem server."""

from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from pathlib import Path

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import TextContent

ROOT = Path(__file__).resolve().parents[1]


def _text(result) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


def _facts() -> list[dict]:
    people = [
        ("Ada", "keeps the launch checklist in ~/notes/launch.md"),
        ("Bao", "owns billing; the repo is git.example/billing"),
        ("Cio", "said not to email customers without approval"),
    ]
    start = date(2024, 1, 1)
    facts: list[dict] = []
    for index in range(48):
        name, detail = people[index % 3]
        facts.append(
            {
                "text": f"Review {index}: {name} {detail}.",
                "date": (start + timedelta(days=index)).isoformat(),
                "tier": "log",
                "scope": "agent",
                "source_assistant": "grok",
            }
        )
    facts.append(
        {
            "text": "Keep this journal when the host trims old log lines.",
            "date": "2024-06-01",
            "tier": "profile",
            "scope": "user",
            "source": "grok",
        }
    )
    facts.append(
        {
            "text": "BOOTSTRAP_TOKEN deploy id deploy_8841 at https://status.example/8841",
            "date": "2024-06-02",
            "tier": "log",
            "scope": "agent",
        }
    )
    facts.append(dict(facts[0]))
    facts.append(
        {
            "text": "LONGSTART " + ("alpha " * 80) + "LONGEND",
            "date": "2024-06-03",
            "tier": "log",
            "scope": "agent",
            "source": "grok",
        }
    )
    assert len(facts) >= 50
    return facts


def test_stdio_bootstrap(tmp_path: Path):
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "grokmem"],
        cwd=str(ROOT),
        env={
            "GROKMEM_DIR": str(tmp_path / "store"),
            "PYTHONPATH": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )

    async def _run() -> str:
        lines: list[str] = []
        async with Client(server) as client:
            info = client.server_info
            lines.append(f"server: {info.name if info else ''} {info.version if info else ''}".rstrip())
            lines.append("--- instructions (first line) ---")
            lines.append((client.instructions or "").splitlines()[0])
            listed = await client.list_tools()
            lines.append("--- tools ---")
            lines.append(", ".join(item.name for item in listed.tools))

            imported = await client.call_tool("grokmem_import", {"facts": _facts()})
            lines.append("--- grokmem_import ---")
            lines.append(_text(imported))
            assert not imported.is_error

            again = await client.call_tool(
                "grokmem_import",
                {
                    "facts": [
                        _facts()[0],
                        {"text": "BOOTSTRAP_TOKEN deploy id deploy_8841 at https://status.example/8841"},
                    ]
                },
            )
            lines.append("--- grokmem_import again ---")
            lines.append(_text(again))
            assert not again.is_error

            loaded = await client.call_tool("grokmem_load", {})
            lines.append("--- grokmem_load ---")
            lines.append(_text(loaded))
            assert not loaded.is_error
            window = re.search(r'window "([^"]+)"', _text(loaded))
            assert window, _text(loaded)

            found = await client.call_tool("grokmem_find", {"pattern": "BOOTSTRAP_TOKEN"})
            lines.append("--- grokmem_find ---")
            lines.append(_text(found))
            assert not found.is_error

            rolled = await client.call_tool(
                "grokmem_rollup",
                {"window": window.group(1), "summary": "Earliest week: Ada, Bao, and Cio, with paths and the approval rule."},
            )
            lines.append("--- grokmem_rollup ---")
            lines.append(_text(rolled))
            assert not rolled.is_error
            summary_id = int(re.search(r"#(\d+)", _text(rolled)).group(1))

            opened = await client.call_tool("grokmem_expand", {"summary_id": summary_id})
            lines.append("--- grokmem_expand ---")
            lines.append(_text(opened))
            assert not opened.is_error

            dropped = await client.call_tool("grokmem_discard", {"summary_id": summary_id})
            lines.append("--- grokmem_discard ---")
            lines.append(_text(dropped))
            assert not dropped.is_error

            rejected = await client.call_tool("grokmem_save", {"text": "y" * 401})
            lines.append("--- grokmem_save 401 characters ---")
            lines.append(f"is_error: {rejected.is_error}")
            lines.append(_text(rejected))
        return "\n".join(lines) + "\n"

    transcript = anyio.run(_run)
    assert "Context loaded." in transcript
    assert "Nothing new" in transcript
    assert "BOOTSTRAP_TOKEN" in transcript
    assert "Too long" in transcript
    assert "is_error: True" in transcript
    assert "critical | profile/user" in transcript
    assert "Withdrew week rollup" in transcript
    assert "Run:" not in transcript
