---
name: higgsfield-credits
description: Record and report Higgsfield credit spend per project. Use whenever Higgsfield MCP generations have just run and need costing, when the Stop hook asks for a sync, when the user asks what they or the team have spent this month, or when the user says which project a folder belongs to.
---

# Higgsfield credit tracking

Every Higgsfield generation made through Claude is recorded: how many credits it cost and the
project it belongs to. Report credits only — never convert to money unless asked. **The folder decides the project** — all credits spent in a chat belong to the
project of the folder that chat is running in, including subfolders. Never tag spend by topic,
by what you were talking about, or per chat.

The script is at `${CLAUDE_PLUGIN_ROOT}/scripts/hf_credits.py`. Run it with `python3`.

## After generations run

A hook already logged each generation the moment it ran, but the *price* only exists inside
the chat, so you must fetch it:

1. Call the Higgsfield MCP `transactions` tool with `size` = number of generations + 10 (max 100).
2. Run `python3 <script> sync --transcript "<this chat's transcript path>"`.
   The Stop hook gives you the transcript path; if you don't have it, run `sync` with no
   `--transcript` and fetch `transactions` again in the same chat.
3. Tell the user one line, e.g. `This run: 144 credits — Reeve`.

If `sync` prints **NEEDS PROJECT**, a folder has no project yet:
- Ask the user plainly: *"What project is `<folder>` for?"* Show the credits involved.
- Then run `python3 <script> project "<folder path>" "<project name>"`.
- Use whatever name the user gives. Don't invent names and don't guess.
- This re-labels past and future charges from that folder, so it's only ever asked once.

If `sync` says generations are still unpriced, fetch the next `transactions` page and sync again.

## When asked what was spent

- This person, this month: `python3 <script> report`
- A specific month: `report --month 2026-10`
- Whole team (reads every `ledger-*.csv` in the shared folder): `report --team`
- Also write a spreadsheet file for the Google Sheet: add `--csv`

The table shows credits, how many clips and images were generated (refunds excluded), and how
many **finished ads** were delivered - counted as video files in each project's `Final` folder. Give it
in chat, plus one line on what stands out. Don't save
reports as files unless asked.

## Renaming or merging projects

When the user says two folders are the same project, or renames one, just run `project` again
for each folder with the new name. Project names are resolved fresh on every report, so old
charges follow the new name automatically. Nothing is rewritten by hand.

## What is not captured

Generations made on the Higgsfield website instead of through Claude have no folder, so they
can't be assigned to a project. If a charge can't be matched to a logged call, it is left out
of the ledger rather than guessed at. Say so if the user asks why a number looks low.
