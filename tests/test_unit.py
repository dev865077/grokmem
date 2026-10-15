"""Direct checks: split, dedupe, length, and week rollups."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from grokmem.instructions import instructions
from grokmem.journal import load_text, pending_windows, split_text
from grokmem.server import discard_rollup, expand_rollup, find_facts, import_facts, mcp, save_fact, write_rollup
from grokmem.store import default_store, read_records, store_dir


def _use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROKMEM_DIR", str(tmp_path))


def test_store_prefers_a_writable_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("GROKMEM_DIR", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr("grokmem.store.WORKSPACE_ROOT", str(workspace))
    monkeypatch.setattr("grokmem.store.WORKSPACE_STORE", str(workspace / ".grokmem"))
    assert store_dir() == workspace / ".grokmem"
    assert default_store() == workspace / ".grokmem"


def test_store_falls_back_when_workspace_is_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("GROKMEM_DIR", raising=False)
    monkeypatch.setattr("grokmem.store.WORKSPACE_ROOT", str(tmp_path / "missing"))
    home = tmp_path / "home"
    monkeypatch.setattr("grokmem.store.Path.home", lambda: home)
    assert store_dir() == home / ".grokmem"


def test_split_keeps_every_character():
    text = ("Café " * 90) + "TAIL"
    assert len(text) > 400
    parts = split_text(text)
    assert len(parts) > 1
    assert all(len(part) <= 400 for part in parts)
    assert "".join(parts) == text


def test_import_dedupes_and_marks_critical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _use(tmp_path, monkeypatch)
    long_text = "LONGSTART " + ("detail " * 70) + "LONGEND"
    first = import_facts(
        [
            {"text": "User likes  tea", "tier": "log", "scope": "user", "date": "2024-05-01"},
            {"text": "user   likes tea", "tier": "profile", "scope": "user", "date": "2024-05-02"},
            {"text": long_text, "tier": "log", "scope": "agent", "source": "grok", "date": "2024-06-03"},
            {"text": "   ", "tier": "note"},
        ]
    )
    assert "Split 1 fact" in first
    assert "Skipped 1 empty" in first
    rows = read_records()
    assert any(row.importance == "critical" and row.tier == "profile" and "tea" in row.text for row in rows)
    grouped = [row for row in rows if "LONGSTART" in row.text or "LONGEND" in row.text or (row.group and row.parts > 1)]
    grouped.sort(key=lambda row: row.part)
    assert "".join(row.text for row in grouped) == long_text
    assert all(len(row.text) <= 400 for row in rows)

    again = import_facts(
        [
            {"text": long_text, "tier": "log"},
            {"text": "User likes tea", "tier": "log"},
        ]
    )
    assert "Nothing new" in again
    assert "2 duplicates" in again
    assert len(read_records()) == len(rows)


def test_save_rejects_over_400_characters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _use(tmp_path, monkeypatch)
    with pytest.raises(ToolError, match="Too long"):
        save_fact("x" * 401)


def test_closed_week_rolls_up_and_current_week_does_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _use(tmp_path, monkeypatch)
    today = dt.date.today().isoformat()
    for index in range(4):
        saved = save_fact(f"still open {index}", date=today)
        assert "rollup" not in saved

    for index in range(4):
        save_fact(f"archived fact {index}", date=f"2024-01-0{index + 1}")

    records = read_records()
    pending = pending_windows(records, dt.date.today())
    assert pending[0] == ("week", "2024-W01")
    assert ("week", week_key_today()) not in pending

    shown = load_text(records, dt.date.today(), limit=3)
    assert shown.ready is False
    assert "Context not loaded yet" in shown.text

    wide = load_text(records, dt.date.today())
    assert "Context loaded." in wide.text
    assert 'window "2024-W01"' in wide.text

    written = write_rollup("2024-W01", "Four archived facts from the first week.")
    assert "Saved week rollup #" in written
    summary_id = int(written.split("#", 1)[1].split(" ", 1)[0])
    opened = expand_rollup(summary_id)
    assert "archived fact 0" in opened
    assert "archived fact 3" in opened

    withdrawn = discard_rollup(summary_id)
    assert "Withdrew week rollup" in withdrawn
    assert "archived fact 0" in find_facts("archived fact 0")
    assert pending_windows(read_records(), dt.date.today())[0] == ("week", "2024-W01")

    with pytest.raises(ToolError, match="Window must look"):
        write_rollup("0-1", "nope")


def test_find_ranks_words_and_accepts_a_broken_pattern(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _use(tmp_path, monkeypatch)
    save_fact("Decisão: o webhook expira na sexta")
    save_fact("The user prefers tea in the morning")
    save_fact("The user owns the launch checklist")
    save_fact("Billing owns the invoice export")

    accent = find_facts("decisao")
    assert "webhook expira" in accent
    assert accent.index("webhook expira") < accent.index("match")

    literal = find_facts("webhook (expira")
    assert "webhook expira" in literal
    assert "Bad pattern" not in literal

    ranked = find_facts("what does the user prefer")
    assert "prefers tea" in ranked
    assert "owns the launch" in ranked
    assert ranked.index("prefers tea") < ranked.index("owns the launch")
    assert "invoice" not in ranked
    assert "invoice export" in find_facts(r"invoice expo+")


def test_pending_is_normal_and_a_correction_is_critical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _use(tmp_path, monkeypatch)
    save_fact("Revisar o export antes da sexta", tier="note", importance="low", tags=["pending"])
    save_fact("Pendente: revisar o webhook", tier="note", importance="low")
    save_fact("Pending: check the webhook", tier="note")
    save_fact("TODO ship the notes export", tier="note")
    save_fact("todos os dias o ritual continua", tier="note")
    save_fact("Pending: ship the export", tier="note", importance="critical")
    save_fact("Correção: o prazo era sexta", tier="note", importance="low")
    save_fact("Correction: the deadline was Friday", importance="low")
    save_fact("Corrected: the port is 43123", tier="note")

    rows = {row.text: row.importance for row in read_records()}
    assert rows["Revisar o export antes da sexta"] == "normal"
    assert rows["Pendente: revisar o webhook"] == "normal"
    assert rows["Pending: check the webhook"] == "normal"
    assert rows["TODO ship the notes export"] == "normal"
    assert rows["todos os dias o ritual continua"] == "low"
    assert rows["Pending: ship the export"] == "critical"
    assert rows["Correção: o prazo era sexta"] == "critical"
    assert rows["Correction: the deadline was Friday"] == "critical"
    assert rows["Corrected: the port is 43123"] == "critical"

    protocol = instructions()
    assert "tier log" in protocol
    assert "Correção:" in protocol
    assert "never stored as low" in protocol
    skill = Path(__file__).parents[1].joinpath("skills/grokmem/SKILL.md").read_text()
    assert "tier `log` for a pending" in skill


def test_load_and_find_mark_retracted_and_resolved_facts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _use(tmp_path, monkeypatch)
    save_fact("The webhook expires on Friday")
    save_fact("The user prefers tea in the morning")
    save_fact("Retracted: The webhook expires on Friday", tags=["retracted"])
    save_fact("Resolved: The user prefers tea in the morning")

    loaded = load_text(read_records(), dt.date.today()).text
    assert "[retracted] The webhook expires on Friday" in loaded
    assert "[resolved] The user prefers tea in the morning" in loaded
    assert "[retracted] Retracted:" not in loaded
    assert "[resolved] Resolved:" not in loaded

    found = find_facts("webhook")
    assert "[retracted] The webhook expires on Friday" in found
    resolved = find_facts("prefers tea")
    assert "[resolved] The user prefers tea in the morning" in resolved


def test_empty_journal_and_listing_do_not_mirror_a_host_store():
    text = load_text([], dt.date.today()).text
    assert text == "The journal is empty. Save durable facts as they come up.\nContext loaded."
    assert "host" not in text.lower()
    description = "Local long-term memory journal for this bot: load at task start, save durable facts as you work"
    assert mcp._lowlevel_server.description == description
    root = Path(__file__).resolve().parents[1]
    manifests = [
        json.loads((root / name).read_text())
        for name in (".cursor-plugin/plugin.json", ".grok-plugin/plugin.json")
    ]
    assert manifests[0] == manifests[1]
    assert manifests[0]["category"] == "productivity"
    assert manifests[0]["tags"] == ["memory", "journal", "notes", "tasks"]
    assert manifests[0]["homepage"] == "https://github.com/dev865077/grokmem"
    assert manifests[0]["repository"] == "https://github.com/dev865077/grokmem"
    blob = "\n".join(
        [
            text,
            description,
            instructions(),
            (root / "skills/grokmem/SKILL.md").read_text(),
            (root / "README.md").read_text(),
        ]
    )
    assert "host store" not in blob.lower()
    assert "mirrors" not in blob.lower()


def week_key_today() -> str:
    year, week, _day = dt.date.today().isocalendar()
    return f"{year}-W{week:02d}"
