# GrokMem

GrokMem is a local journal for a Grok Bot. It keeps decisions, corrections, ids, and pendings on that bot's own computer. There is no external server, no account, and nothing to configure.

The journal is append-only. Each fact is one JSON line in `journal.jsonl`, with an id, a date, a tier, a scope, a source, an importance, tags, and the text. A fact is never rewritten. A bad week or month summary is withdrawn by appending a `drop` line. The bot writes those summaries itself when a tool asks for one. Nothing runs in the background.

Inspired by the ideas in OptMem by Victor Taelin. This is a separate implementation.

## Install

Install the plugin from the Grok Bot, Cursor, or Grok Build marketplace. The manifest starts the server with `uvx`. No token, no URL, and no environment variable is required.

To run the same server from this repository:

```bash
uvx --from . grokmem
```

That needs Python 3.11 or newer, and `uv`. The only network use is `uvx` fetching the pinned `mcp` package (`mcp==2.3.0`) the first time it is not already cached. After that, the server speaks stdio and does not open a connection. It sends no telemetry and reads no credentials.

## Where data is stored

`GROKMEM_DIR` selects the directory. When it is unset, the journal goes to `/workspace/.grokmem` if `/workspace` exists and is writable, which is the bot's cloud computer. Otherwise it goes to `~/.grokmem`. The directory is created on the first write.

## Tools

| Tool | What it does |
| --- | --- |
| `grokmem_load` | Read the journal. Call it at the start of a task. |
| `grokmem_save` | Store one fact, at most 400 characters. |
| `grokmem_import` | Store a batch. Longer text is split without dropping characters. |
| `grokmem_find` | Search facts and active summaries. Words match with OR, ignoring case and accents. |
| `grokmem_expand` | Open a week or month summary down to the lines it covers. |
| `grokmem_rollup` | Write the next week or month summary, at most 600 characters. |
| `grokmem_discard` | Withdraw a bad summary so it can be written again. Facts stay. |

A profile fact, text that starts with `!`, or a correction (`Correção:`, `Correction:`, `Corrected:`) is stored as critical and listed at the top of a read. A pending (`Pendente:`, `Pending:`, `TODO`, or the tag `pending`) uses tier `log` and is stored as normal, never low. An exact or normalized duplicate is skipped unless the new copy is more critical.

The skill in `skills/grokmem/SKILL.md` tells the bot when to load and what to save. The same protocol is sent as the server instructions.

## Tests

```bash
python3 -m pytest -q
```

## License

Copyright (C) 2026 Pedro Martins.

GPL-3.0-or-later. See [LICENSE](LICENSE).
