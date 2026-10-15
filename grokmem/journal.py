"""Facts, dedupe, and week/month rollups.

The journal is inspired by the idea of short entries plus summaries the agent
writes itself. Windows are calendar weeks and calendar months, not a tree of
fixed-size blocks.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

from grokmem.brand import tool
from grokmem.store import Record

FACT_LIMIT = 400
SUMMARY_LIMIT = 600
WEEK_MIN = 4
MONTH_MIN = 4
VIEW_LIMIT = 80
PAGE_SIZE = 100

TIERS = {"profile", "log", "note"}
SCOPES = {"conversation", "agent", "user"}
IMPORTANCE = {"critical": 2, "normal": 1, "low": 0}
_WEEK = re.compile(r"^\d{4}-W\d{2}$")
_MONTH = re.compile(r"^\d{4}-\d{2}$")
_TAG = re.compile(r"[^a-z0-9_-]+")
_REGEX_META = re.compile(r"[.\\*+?^${}\[\]()|\\]")
_COVER_MIN = 8


def fold(text: str) -> str:
    stripped = "".join(ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn")
    return stripped.casefold()


_STOPWORDS = frozenset(
    fold(word)
    for word in (
        "a an the and or but if then else when while at by for with about against "
        "between into through during before after above below to from up down in "
        "out on off over under again further once here there all any both each few "
        "more most other some such no nor not only own same so than too very can "
        "will just should now of is are was were be been being have has had do does "
        "did doing would could may might must shall this that these those i you he "
        "she it we they me him her us them my your his its our their what which who "
        "whom whose where why how am as until also "
        "o os as um uma uns umas de do da dos das em no na nos nas por para com sem "
        "sob sobre entre e ou mas que se como quando onde qual quais quem porque "
        "porquê pq ao aos à às pelo pela pelos pelas este esta estes estas esse "
        "essa esses essas aquele aquela aquilo isto isso eu tu ele ela nós vós eles "
        "elas me te lhe lhes meu minha teu tua seu sua nosso nossa vosso vossa não "
        "sim já ainda também muito pouco mais menos bem mal só apenas ser estar ter "
        "haver foi era são está estão tem tinha fazer faz feito num numa você vocês "
        "pra pro até após desde é eram"
    ).split()
)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def digest(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:12]


def rank(importance: str) -> int:
    return IMPORTANCE.get(importance, 1)


def _is_correction(text: str) -> bool:
    start = fold(text.lstrip())
    return start.startswith(("correcao:", "correction:", "corrected:"))


def _is_pending(text: str, tags: list[str]) -> bool:
    if "pending" in tags:
        return True
    start = fold(text.lstrip())
    if start.startswith(("pendente:", "pending:")):
        return True
    return start == "todo" or start.startswith(("todo:", "todo "))


def derive_importance(
    text: str,
    tier: str | None,
    explicit: str | None,
    tags: list[str] | None = None,
) -> str:
    cleaned = clean_tags(tags)
    if tier == "profile" or text.lstrip().startswith("!") or _is_correction(text):
        return "critical"
    if explicit == "critical":
        return "critical"
    if _is_pending(text, cleaned):
        return "normal"
    if explicit in IMPORTANCE:
        return explicit
    if tier == "note":
        return "low"
    return "normal"


def marker_kind(text: str) -> str | None:
    start = fold(text.lstrip())
    if start.startswith("retracted:"):
        return "retracted"
    if start.startswith("resolved:"):
        return "resolved"
    return None


def fact_marks(records: list[Record]) -> dict[int, str]:
    """Later Retracted: or Resolved: text covers an earlier fact it quotes."""
    facts = [record for record in records if record.kind == "fact"]
    marks: dict[int, str] = {}
    for later in facts:
        kind = marker_kind(later.text)
        if kind is None:
            continue
        body = fold(normalize(later.text))
        for earlier in facts:
            if earlier.id >= later.id:
                continue
            old = fold(normalize(earlier.text))
            if len(old) < _COVER_MIN or old not in body:
                continue
            marks[earlier.id] = kind
    return marks


def query_words(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", fold(text))
    content = [token for token in tokens if token not in _STOPWORDS and (len(token) > 1 or token.isdigit())]
    if not content:
        content = list(tokens)
    chosen: list[str] = []
    for token in content:
        if token not in chosen:
            chosen.append(token)
    return chosen


def _as_regex(pattern: str) -> re.Pattern[str] | None:
    if not _REGEX_META.search(pattern):
        return None
    try:
        return re.compile(fold(pattern), re.IGNORECASE)
    except re.error:
        return None


def clean_tags(tags: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    for tag in tags or []:
        token = _TAG.sub("", tag.strip().lower())[:24]
        if token and token not in cleaned:
            cleaned.append(token)
        if len(cleaned) == 8:
            break
    return cleaned


def clean_source(source: str | None) -> str:
    return re.sub(r"\s+", " ", (source or "").replace("\n", " ")).strip()[:40]


def check_choice(value: str, allowed: set[str], label: str) -> str:
    if value not in allowed:
        names = ", ".join(sorted(allowed))
        raise ValueError(f"{label} must be one of {names}, not {value!r}.")
    return value


def parse_date(value: str, where: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        raise ValueError(f"{where} must be YYYY-MM-DD, not {value!r}.")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{where} {value} is not a real date.") from exc
    return value


def week_key(iso_date: str) -> str:
    year, week, _day = dt.date.fromisoformat(iso_date).isocalendar()
    return f"{year}-W{week:02d}"


def month_key(iso_date: str) -> str:
    return iso_date[:7]


def split_text(text: str, limit: int = FACT_LIMIT) -> list[str]:
    """Split on character count. ''.join(parts) == text. Nothing is dropped."""
    if limit < 1:
        raise ValueError("The length limit must be at least 1.")
    parts: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            parts.append(rest)
            break
        window = rest[:limit]
        space = window.rfind(" ")
        if space >= limit // 3:
            piece = window[: space + 1]
        else:
            piece = window
        if not piece:
            piece = rest[:1]
        parts.append(piece)
        rest = rest[len(piece) :]
        if len(parts) > 200:
            raise ValueError("That fact is too large to import in one call.")
    if "".join(parts) != text:
        raise RuntimeError("Splitting a fact dropped characters.")
    return parts


@dataclass
class Known:
    rank_by_norm: dict[str, int] = field(default_factory=dict)
    rank_by_group: dict[str, int] = field(default_factory=dict)
    id_by_norm: dict[str, int] = field(default_factory=dict)
    id_by_group: dict[str, int] = field(default_factory=dict)

    def observe(self, record: Record) -> None:
        if record.kind != "fact":
            return
        level = rank(record.importance)
        if record.group:
            if level >= self.rank_by_group.get(record.group, -1):
                self.rank_by_group[record.group] = level
                self.id_by_group[record.group] = record.id
        if record.parts == 1:
            key = normalize(record.text)
            if key and level >= self.rank_by_norm.get(key, -1):
                self.rank_by_norm[key] = level
                self.id_by_norm[key] = record.id

    def find(self, text: str) -> tuple[int, int | None]:
        key = normalize(text)
        group = digest(key)
        found_rank = -1
        found_id: int | None = None
        if key in self.rank_by_norm:
            found_rank = self.rank_by_norm[key]
            found_id = self.id_by_norm.get(key)
        if group in self.rank_by_group and self.rank_by_group[group] >= found_rank:
            found_rank = self.rank_by_group[group]
            found_id = self.id_by_group.get(group)
        return found_rank, found_id

    def remember_text(self, text: str, importance: str, record_id: int, group: str) -> None:
        level = rank(importance)
        key = normalize(text)
        if level >= self.rank_by_norm.get(key, -1):
            self.rank_by_norm[key] = level
            self.id_by_norm[key] = record_id
        if level >= self.rank_by_group.get(group, -1):
            self.rank_by_group[group] = level
            self.id_by_group[group] = record_id


def known_from(records: list[Record]) -> Known:
    known = Known()
    for record in records:
        known.observe(record)
    return known


def dropped_ids(records: list[Record]) -> set[int]:
    return {record.target for record in records if record.kind == "drop"}


def active_summaries(records: list[Record]) -> dict[str, Record]:
    gone = dropped_ids(records)
    best: dict[str, Record] = {}
    for record in records:
        if record.kind != "summary" or record.id in gone:
            continue
        key = f"{record.level}:{record.window}"
        current = best.get(key)
        if current is None or record.id > current.id:
            best[key] = record
    return best


def facts_of(records: list[Record]) -> list[Record]:
    return [record for record in records if record.kind == "fact"]


def _by_week(facts: list[Record]) -> dict[str, list[Record]]:
    buckets: dict[str, list[Record]] = {}
    for fact in facts:
        buckets.setdefault(week_key(fact.date), []).append(fact)
    for bucket in buckets.values():
        bucket.sort(key=lambda fact: (fact.date, fact.id))
    return buckets


def pending_windows(records: list[Record], today: dt.date) -> list[tuple[str, str]]:
    """Oldest first. Each item is ('week'|'month', window)."""
    facts = facts_of(records)
    summaries = active_summaries(records)
    current_week = week_key(today.isoformat())
    current_month = today.strftime("%Y-%m")
    weeks: list[str] = []
    for week, items in _by_week(facts).items():
        if week >= current_week or len(items) < WEEK_MIN:
            continue
        if f"1:{week}" not in summaries:
            weeks.append(week)
    weeks.sort()
    covered: set[int] = set()
    for summary in summaries.values():
        if summary.level == 1:
            covered.update(summary.covers)
    months: list[str] = []
    by_month: dict[str, list[Record]] = {}
    for fact in facts:
        by_month.setdefault(month_key(fact.date), []).append(fact)
    for month, items in by_month.items():
        if month >= current_month or len(items) < MONTH_MIN:
            continue
        if f"2:{month}" in summaries:
            continue
        ready = True
        saw_week = False
        for week, week_facts in _by_week(items).items():
            if week >= current_week or len(week_facts) < WEEK_MIN:
                continue
            saw_week = True
            if any(fact.id not in covered for fact in week_facts):
                ready = False
                break
        if ready and saw_week:
            months.append(month)
    months.sort()
    return [("week", week) for week in weeks] + [("month", month) for month in months]


def render_fact(fact: Record, mark: str | None = None) -> str:
    who = f" | {fact.source}" if fact.source else ""
    tags = f" | {','.join(fact.tags)}" if fact.tags else ""
    part = f" | part {fact.part}/{fact.parts}" if fact.parts > 1 else ""
    flag = f"[{mark}] " if mark else ""
    return (
        f"#{fact.id} | {fact.date} | {fact.importance} | {fact.tier}/{fact.scope}"
        f"{who}{tags}{part} | {flag}{fact.text}"
    )


def _show_fact(fact: Record, marks: dict[int, str]) -> str:
    return render_fact(fact, marks.get(fact.id))


def render_summary(summary: Record) -> str:
    label = "week" if summary.level == 1 else "month"
    return f"#{summary.id} | {label} {summary.window} | {summary.text}"


def _window_lines(records: list[Record], kind: str, window: str) -> list[str]:
    facts = facts_of(records)
    summaries = active_summaries(records)
    marks = fact_marks(records)
    if kind == "week":
        rows = [fact for fact in facts if week_key(fact.date) == window]
        rows.sort(key=lambda fact: (fact.date, fact.id))
        return [_show_fact(fact, marks) for fact in rows]
    lines: list[str] = []
    for summary in summaries.values():
        if summary.level != 1:
            continue
        if any(fact.date.startswith(window) for fact in facts if fact.id in summary.covers):
            lines.append(render_summary(summary))
    return lines


def _cover_ids(records: list[Record], kind: str, window: str) -> list[int]:
    if kind == "week":
        rows = [fact for fact in facts_of(records) if week_key(fact.date) == window]
        return [fact.id for fact in sorted(rows, key=lambda fact: (fact.date, fact.id))]
    ids: list[int] = []
    for summary in active_summaries(records).values():
        if summary.level != 1:
            continue
        if any(fact.date.startswith(window) for fact in facts_of(records) if fact.id in summary.covers):
            ids.append(summary.id)
    return sorted(ids)


def rollup_request(records: list[Record], today: dt.date) -> str | None:
    pending = pending_windows(records, today)
    if not pending:
        return None
    kind, window = pending[0]
    lines = _window_lines(records, kind, window)
    label = "week" if kind == "week" else "month"
    left = len(pending) - 1
    tail = f"\n{left} rollup{'s' if left != 1 else ''} remain after this one." if left else ""
    body = "\n".join(lines)
    return (
        f"Write a {label} rollup for {window}. Put critical facts first. "
        f"Keep names, ids, links, paths, decisions, and retractions. Invent nothing.\n\n"
        f"{body}\n\n"
        f'Call {tool("rollup")} with window "{window}" and a summary of at most '
        f"{SUMMARY_LIMIT} characters.{tail}"
    )


@dataclass
class LoadResult:
    text: str
    ready: bool


def _entries(records: list[Record], today: dt.date) -> list[str]:
    facts = facts_of(records)
    summaries = active_summaries(records)
    marks = fact_marks(records)
    current_week = week_key(today.isoformat())
    pinned = [fact for fact in facts if fact.importance == "critical"]
    pinned.sort(key=lambda fact: (fact.date, fact.id))
    pinned_ids = {fact.id for fact in pinned}
    lines: list[str] = []
    if pinned:
        lines.append("Pinned")
        lines.extend(_show_fact(fact, marks) for fact in pinned)
    recent = [
        fact
        for fact in facts
        if week_key(fact.date) == current_week and fact.id not in pinned_ids
    ]
    recent.sort(key=lambda fact: (fact.date, fact.id))
    if recent:
        lines.append("This week")
        lines.extend(_show_fact(fact, marks) for fact in recent)
    earlier: list[str] = []
    months = sorted({month_key(fact.date) for fact in facts if week_key(fact.date) != current_week})
    emitted: set[str] = set()
    for month in months:
        month_summary = summaries.get(f"2:{month}")
        if month_summary is not None:
            earlier.append(render_summary(month_summary))
            emitted.update(week_key(fact.date) for fact in facts if month_key(fact.date) == month)
            continue
        weeks = sorted({week_key(fact.date) for fact in facts if month_key(fact.date) == month})
        for week in weeks:
            if week == current_week or week in emitted:
                continue
            emitted.add(week)
            week_summary = summaries.get(f"1:{week}")
            if week_summary is not None:
                earlier.append(render_summary(week_summary))
                continue
            raw = [
                fact
                for fact in facts
                if week_key(fact.date) == week and fact.id not in pinned_ids
            ]
            raw.sort(key=lambda fact: (fact.date, fact.id))
            earlier.extend(_show_fact(fact, marks) for fact in raw)
    if earlier:
        lines.append("Earlier")
        lines.extend(earlier)
    return lines


def load_text(
    records: list[Record],
    today: dt.date,
    page: int = 1,
    limit: int = VIEW_LIMIT,
    page_size: int = PAGE_SIZE,
) -> LoadResult:
    if page < 1:
        raise ValueError("page starts at 1.")
    pending = rollup_request(records, today)
    body_lines = _entries(records, today)
    content_lines = [line for line in body_lines if not line.startswith(("Pinned", "This week", "Earlier"))]
    if len(content_lines) > limit and pending:
        return LoadResult(
            text=pending + "\nContext not loaded yet. Finish the rollup above, then call "
            f'{tool("load")} again.',
            ready=False,
        )
    pages = [body_lines[index : index + page_size] for index in range(0, len(body_lines), page_size)] or [[]]
    if page > len(pages):
        raise ValueError(f"No page {page}. The journal view has {len(pages)} page(s).")
    chosen = list(pages[page - 1])
    if page < len(pages):
        chosen.append(f'Context continues. Call {tool("load")} with page={page + 1}.')
        return LoadResult(text="\n".join(chosen), ready=False)
    if pending:
        chosen.append(pending)
    if not records:
        chosen.append("The journal is empty. Save durable facts as they come up.")
    chosen.append("Context loaded.")
    return LoadResult(text="\n".join(chosen), ready=True)


def prepare_rollup(records: list[Record], window: str, summary: str, today: dt.date) -> Record:
    raw = (window or "").strip()
    if _WEEK.fullmatch(raw):
        kind = "week"
        level = 1
    elif _MONTH.fullmatch(raw):
        kind = "month"
        level = 2
    else:
        raise ValueError(f"Window must look like 2024-W01 or 2024-01, not {window!r}.")
    text = (summary or "").strip()
    if not text:
        raise ValueError("A rollup needs a summary.")
    if len(text) > SUMMARY_LIMIT:
        raise ValueError(
            f"Too long: {len(text)} characters. A rollup summary may be {SUMMARY_LIMIT}."
        )
    pending = pending_windows(records, today)
    if not pending:
        raise ValueError("No rollup is waiting.")
    if pending[0] != (kind, raw):
        nxt = pending[0][1]
        raise ValueError(f"Next window is {nxt}, not {raw}. Windows are written oldest first.")
    covers = _cover_ids(records, kind, raw)
    if not covers:
        raise ValueError(f"Nothing to roll up for {raw}.")
    return Record(
        id=0,
        kind="summary",
        date=today.isoformat(),
        text=text,
        tier="log",
        scope="agent",
        importance="normal",
        level=level,
        window=raw,
        covers=covers,
    )


def search_records(records: list[Record], pattern: str, cap: int = 40) -> str:
    query = (pattern or "").strip()
    if not query:
        raise ValueError(f'Pass a pattern to {tool("find")}.')
    compiled = _as_regex(query)
    words = [] if compiled is not None else query_words(query)
    if compiled is None and not words:
        return "No match."
    gone = dropped_ids(records)
    marks = fact_marks(records)
    scored: list[tuple[int, int, str]] = []
    for record in records:
        if record.kind == "drop" or record.id in gone:
            continue
        blob = " ".join(
            [record.text, record.source, record.window, " ".join(record.tags), record.tier, record.scope]
        )
        folded = fold(blob)
        if compiled is not None:
            if compiled.search(folded) is None:
                continue
            score = 1
        else:
            score = sum(1 for word in words if word in folded)
            if score <= 0:
                continue
        line = render_summary(record) if record.kind == "summary" else _show_fact(record, marks)
        scored.append((score, record.id, line))
    if not scored:
        return "No match."
    scored.sort(key=lambda item: (-item[0], -item[1]))
    shown = scored[:cap]
    lines = [item[2] for item in shown]
    if len(shown) < len(scored):
        lines.append(f"Showing {len(shown)} of {len(scored)} matches. Narrow the query.")
    else:
        noun = "match" if len(scored) == 1 else "matches"
        lines.append(f"{len(scored)} {noun}.")
    return "\n".join(lines)


def expand_summary(records: list[Record], summary_id: int) -> str:
    summaries = active_summaries(records)
    summary = next((item for item in summaries.values() if item.id == summary_id), None)
    if summary is None:
        raise ValueError(f"No active rollup #{summary_id}.")
    label = "week" if summary.level == 1 else "month"
    lines = [f"Rollup #{summary.id} covers {label} {summary.window}."]
    if summary.level == 1:
        wanted = set(summary.covers)
        rows = [fact for fact in facts_of(records) if fact.id in wanted]
        rows.sort(key=lambda fact: (fact.date, fact.id))
        marks = fact_marks(records)
        lines.extend(_show_fact(fact, marks) for fact in rows)
    else:
        for covered in summary.covers:
            child = next((item for item in summaries.values() if item.id == covered), None)
            if child is not None:
                lines.append(render_summary(child))
        lines.append(f'Call {tool("expand")} on a week rollup to read the facts under it.')
    return "\n".join(lines)


def plan_drop(records: list[Record], summary_id: int, today: dt.date) -> Record:
    summaries = active_summaries(records)
    summary = next((item for item in summaries.values() if item.id == summary_id), None)
    if summary is None:
        raise ValueError(f"No active rollup #{summary_id}. Facts stay in the journal either way.")
    return Record(id=0, kind="drop", date=today.isoformat(), target=summary.id, window=summary.window)


@dataclass
class Incoming:
    text: str
    date: str | None = None
    tier: str | None = None
    scope: str | None = None
    source: str | None = None
    importance: str | None = None
    tags: list[str] | None = None


@dataclass
class IngestPlan:
    records: list[Record] = field(default_factory=list)
    accepted: int = 0
    skipped: int = 0
    empty: int = 0
    split_facts: int = 0


def plan_ingest(items: list[Incoming], records: list[Record], today: dt.date) -> IngestPlan:
    known = known_from(records)
    plan = IngestPlan()
    for index, item in enumerate(items):
        raw = (item.text or "").strip()
        if not raw:
            plan.empty += 1
            continue
        tier = check_choice((item.tier or "log").lower(), TIERS, f"facts[{index}].tier")
        scope = check_choice((item.scope or "agent").lower(), SCOPES, f"facts[{index}].scope")
        explicit = item.importance.lower() if isinstance(item.importance, str) else item.importance
        item = Incoming(
            text=item.text,
            date=item.date,
            tier=tier,
            scope=scope,
            source=item.source,
            importance=explicit,
            tags=item.tags,
        )
        if item.importance not in (None, "") and item.importance not in IMPORTANCE:
            raise ValueError(
                f"facts[{index}].importance must be critical, normal, or low, not {item.importance!r}."
            )
        tags = clean_tags(item.tags)
        importance = derive_importance(raw, tier, item.importance, tags)
        when = parse_date(item.date, f"facts[{index}].date") if item.date else today.isoformat()
        source = clean_source(item.source)
        stored_rank, existing = known.find(raw)
        if stored_rank >= rank(importance):
            plan.skipped += 1
            continue
        pieces = split_text(raw, FACT_LIMIT)
        group = digest(raw)
        if len(pieces) > 1:
            plan.split_facts += 1
        for part, piece in enumerate(pieces, 1):
            plan.records.append(
                Record(
                    id=0,
                    kind="fact",
                    date=when,
                    text=piece,
                    tier=tier,
                    scope=scope,
                    source=source,
                    importance=importance,
                    tags=tags,
                    part=part,
                    parts=len(pieces),
                    group=group,
                )
            )
        known.remember_text(raw, importance, existing or 0, group)
        plan.accepted += 1
    return plan


def validate_save(text: str) -> str:
    body = (text or "").strip()
    if not body:
        raise ValueError("Empty. A fact needs text.")
    if len(body) > FACT_LIMIT:
        raise ValueError(
            f"Too long: {len(body)} characters. The limit is {FACT_LIMIT}. "
            f'Call {tool("import")} with this fact so it is split without dropping characters.'
        )
    return body
