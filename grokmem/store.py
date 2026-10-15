"""Append-only JSONL journal.

Each line is one record. Facts are never rewritten. A bad rollup is withdrawn
by appending a drop record that names its id.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from grokmem.brand import ENV_DIR, FALLBACK_STORE, WORKSPACE_ROOT, WORKSPACE_STORE

try:
    import fcntl
except ImportError:  # pragma: no cover - this plugin targets POSIX
    fcntl = None


def default_store() -> Path:
    workspace = Path(WORKSPACE_ROOT)
    if workspace.is_dir() and os.access(workspace, os.W_OK):
        return Path(WORKSPACE_STORE)
    return Path.home() / Path(FALLBACK_STORE).name


def store_dir() -> Path:
    raw = os.environ.get(ENV_DIR)
    if raw:
        return Path(os.path.abspath(os.path.expanduser(raw)))
    return default_store()


@dataclass
class Record:
    id: int
    kind: str
    date: str
    text: str = ""
    tier: str = "log"
    scope: str = "agent"
    source: str = ""
    importance: str = "normal"
    tags: list[str] = field(default_factory=list)
    part: int = 1
    parts: int = 1
    group: str = ""
    level: int = 0
    window: str = ""
    covers: list[int] = field(default_factory=list)
    target: int = 0

    def to_dict(self) -> dict:
        if self.kind == "drop":
            return {"id": self.id, "kind": "drop", "date": self.date, "target": self.target}
        data = {
            "id": self.id,
            "kind": self.kind,
            "date": self.date,
            "text": self.text,
            "tier": self.tier,
            "scope": self.scope,
            "source": self.source,
            "importance": self.importance,
            "tags": self.tags,
        }
        if self.kind == "fact" and self.parts > 1:
            data["part"] = self.part
            data["parts"] = self.parts
            data["group"] = self.group
        elif self.kind == "fact" and self.group:
            data["group"] = self.group
        if self.kind == "summary":
            data["level"] = self.level
            data["window"] = self.window
            data["covers"] = self.covers
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Record:
        return cls(
            id=int(data["id"]),
            kind=str(data.get("kind") or "fact"),
            date=str(data.get("date") or ""),
            text=str(data.get("text") or ""),
            tier=str(data.get("tier") or "log"),
            scope=str(data.get("scope") or "agent"),
            source=str(data.get("source") or ""),
            importance=str(data.get("importance") or "normal"),
            tags=[str(tag) for tag in (data.get("tags") or [])],
            part=int(data.get("part") or 1),
            parts=int(data.get("parts") or 1),
            group=str(data.get("group") or ""),
            level=int(data.get("level") or 0),
            window=str(data.get("window") or ""),
            covers=[int(item) for item in (data.get("covers") or [])],
            target=int(data.get("target") or 0),
        )


def journal_path(directory: Path | None = None) -> Path:
    return (directory or store_dir()) / "journal.jsonl"


@contextmanager
def _locked(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    handle = open(directory / ".lock", "a", encoding="utf-8")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _read_unlocked(directory: Path) -> list[Record]:
    path = journal_path(directory)
    if not path.is_file():
        return []
    rows: list[Record] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(Record.from_dict(json.loads(line)))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"journal.jsonl line {number} is not a record: {exc}") from exc
    return rows


def read_records(directory: Path | None = None) -> list[Record]:
    directory = directory or store_dir()
    with _locked(directory):
        return _read_unlocked(directory)


def append_records(records: list[Record], directory: Path | None = None) -> list[Record]:
    """Assign ids and append. Returns the stored records in order."""
    if not records:
        return []
    directory = directory or store_dir()
    with _locked(directory):
        existing = _read_unlocked(directory)
        nxt = max((row.id for row in existing), default=0) + 1
        stored: list[Record] = []
        path = journal_path(directory)
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                record.id = nxt
                nxt += 1
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
                stored.append(record)
            handle.flush()
            os.fsync(handle.fileno())
        return stored
