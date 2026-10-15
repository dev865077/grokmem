---
name: grokmem
description: >-
  The user's local GrokMem journal, used alongside UpdateMemory, never instead of it.
  At the start of a conversation call grokmem_load before answering. Call grokmem_find
  whenever you lack context about the user, their projects, past decisions, people, or
  ids, before answering or asking the user. Each time you call UpdateMemory with action
  "write", also save the same fact with grokmem_save; with "forget", save
  "Retracted: <old text>" with tag retracted. Save decisions, corrections, ids, and
  pendings while you work. If the user asks to activate or enable GrokMem, call
  UpdateMemory with type shared_profile to store: "Every assistant should call grokmem_load at the start of a conversation, call grokmem_find whenever it lacks context about the user, their projects, past decisions, people, or ids (before answering or asking the user), and, whenever it calls UpdateMemory, also save the same fact with grokmem_save. GrokMem complements normal memory and never replaces it." Then confirm it is active.
---

# GrokMem

GrokMem is a journal on this computer. It keeps the wording and the date of facts a conversation would otherwise lose. Host memory tools, when they exist, stay in use. This journal does not depend on them.

## Activate

If the user asks to activate, enable, or turn on GrokMem, call UpdateMemory with action `write` and type `shared_profile`, with this text:

`Every assistant should call grokmem_load at the start of a conversation, call grokmem_find whenever it lacks context about the user, their projects, past decisions, people, or ids (before answering or asking the user), and, whenever it calls UpdateMemory, also save the same fact with grokmem_save. GrokMem complements normal memory and never replaces it.`

Then tell the user GrokMem is active for all of their bots, and that they can ask to turn it off, which forgets that line.

## Pair with UpdateMemory

GrokMem runs next to your built-in memory. Keep using UpdateMemory exactly as before. Every time you call it:

- action `write`: also call `grokmem_save` with the same sentence as `text`, and the same `tier` and `scope`.
- action `forget`: also call `grokmem_save` with `text` = `Retracted: <the exact old text>` and `tags` = `["retracted"]`.

Do both in the same step. The tools may live in a dynamic namespace whose tools start with `grokmem_`; discover it once per conversation, then call.

## Start of a task

Call `grokmem_load` before other work, and do what it prints.

- If it says the context is not loaded yet, write the rollup it names with `grokmem_rollup`, then call `grokmem_load` again.
- If it says the context continues, ask for the next page.

## While you work

Save as you go. If you are unsure, save. Use `grokmem_save` for one fact, at most 400 characters. Use `grokmem_import` for a batch. Text longer than 400 characters is split without dropping characters.

Capture decisions, corrections, names, ids, links, paths, task outcomes, failures and why they failed, and anything still pending. Save again when a task finishes and when the chat is long enough that a summary is likely. Skip a fact this journal already returned.

Set tier to `profile`, `log`, or `note`, and scope to `conversation`, `agent`, or `user`. Use tier `log` for a pending. Tier `note` is a low aside, and a pending must not be stored as low.

A profile fact, text that starts with `!`, and a correction are critical. A correction starts with `Correção:`, `Correction:`, or `Corrected:`. A pending starts with `Pendente:`, `Pending:`, or `TODO`, or carries the tag `pending`. It is stored as normal, or critical when one of the critical rules applies. It is never stored as low.

To record that a fact no longer holds, call `grokmem_save` with text that starts with `Retracted:` and tag `retracted`, including the old wording. Start with `Resolved:` when the fact is finished instead. Load and find then mark that earlier fact. Do not delete the journal line.

## Lookup

Call `grokmem_find` with the words you remember. Matching ignores case and accents, so `decisao` matches `decisão`. Common words in Portuguese and English are dropped, and hits are ranked by how many words match. A question such as `what does the user prefer` returns the relevant fact. The text is literal unless it is a valid regular expression. If a hit is a week or month rollup, call `grokmem_expand` on its id.

## Rollups

A rollup is a summary you write, at most 600 characters, via `grokmem_rollup`. Put critical facts first. Keep names, ids, links, paths, decisions, and retractions. Invent nothing. Windows are a calendar week, then a calendar month once its weeks are done, oldest first. `grokmem_discard` only withdraws a bad rollup so it can be written again. It does not remove facts.

## Subagents

Parallel sessions that share this journal may all write. A spawned subagent must not. When you create one, tell it: You are a subagent. Do not call grokmem tools.
