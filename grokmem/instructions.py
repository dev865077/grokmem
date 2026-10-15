"""Model-facing protocol. Written for this plugin; the product name lives in brand.py."""

from grokmem.brand import PRODUCT, tool


def instructions() -> str:
    load, save, rollup = tool("load"), tool("save"), tool("rollup")
    find, expand, discard, ingest = tool("find"), tool("expand"), tool("discard"), tool("import")
    return f"""\
This journal is a local archive on this computer. It keeps the wording and the date of facts a conversation would otherwise lose. Host memory tools, when the host has them, stay available. This journal does not depend on them.

Call {load} at the start of every task, before other work, and do what it prints. If it says the context is not loaded yet, write the rollup it names and call {load} again. If it says the context continues, ask for the next page.

Save as you go. If you are unsure, save. Use {save} for one fact and {ingest} for a batch. Set tier to profile, log, or note, and scope to conversation, agent, or user. Use tier log for a pending. Tier note is a low aside, and a pending must not be stored as low.

Importance becomes critical for a profile fact, for text that starts with !, and for a correction. A correction starts with "Correção:", "Correction:", or "Corrected:". Otherwise set importance to critical yourself when the fact must survive a later summary. A pending starts with "Pendente:", "Pending:", or "TODO", or carries the tag pending. It is stored as normal, or critical when one of the critical rules applies. It is never stored as low. Add short tags when they will help a later search.

Capture decisions, corrections, names, ids, links, paths, task outcomes, failures and why they failed, and anything still pending. Save again when a task finishes and when the chat is long enough that a summary is likely. Skip a fact this journal already returned.

To record that a fact no longer holds, do not delete the journal. Call {save} with text that starts with "Retracted:" and tag retracted, including the exact old wording. Start with "Resolved:" when the fact is finished instead. Load and find then mark that earlier fact. This journal keeps both versions.

To look something up, call {find}. The query is split into words and matched with OR, ignoring case and accents, so "decisao" matches "decisão". Common words in Portuguese and English are dropped, and hits are ranked by how many words match, so a question such as "what does the user prefer" returns the relevant fact. The text is literal unless it is a valid regular expression, so a fragment such as "webhook (expira" does not fail. If a hit is a week or month rollup, call {expand} on its id. This journal holds the timeline and the wording a summary dropped.

A rollup is a summary you write yourself, at most 600 characters, via {rollup}. Put critical facts first. Keep names, ids, links, paths, decisions, and retractions. Invent nothing. Windows are answered oldest first: a calendar week, then a calendar month once its weeks are done. {discard} only withdraws a bad rollup so it can be written again. It does not remove facts.

One {save} is at most 400 characters. If the text is longer, call {ingest} and it will be split without dropping characters.

Parallel sessions that share this journal may all write. A spawned subagent must not. When you create one, tell it: You are a subagent. Do not call {PRODUCT} tools.
"""
