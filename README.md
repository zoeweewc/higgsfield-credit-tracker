# Higgsfield Credit Tracker

Records every Higgsfield generation made through Claude — how many credits it cost, and which
project it was for — so the team can see where the month's credits went.

**The folder you work in decides the project.** Work inside the `Reeve` folder and it counts as
Reeve, including every subfolder. Nobody has to label anything while they work.

---

## Install (once, per person)

In Claude Code, run:

```
/plugin marketplace add zoeweewc/higgsfield-credit-tracker
```

```
/plugin install higgsfield-credits@funnelduo-tools
```

Then tell it your first name. It finds the team's shared Drive folder by itself, and reads your
own past chats from the start of this month so your existing spend is already in there:

```bash
python3 ~/.claude/plugins/cache/*/higgsfield-credits/*/scripts/hf_credits.py setup --user <your first name>
```

If it can't find the shared folder, the folder hasn't been shared with you yet, or Google Drive
for desktop hasn't finished syncing. Wait for it, then run the line again.

Restart Claude Code. That's the whole setup.

---

## What happens after that

**While you work:** nothing. Keep making videos as usual.

**When you finish a chat:** if that chat made any Higgsfield videos or images, Claude prices
them before the chat can end, and tells you one line like
`This run: 144 credits — Reeve`.

**If you worked in a folder nobody has claimed yet,** Claude asks once: *"What project is this
folder for?"* Answer in your own words — `Rawson`, `Nike launch`, anything. It's remembered for
that folder forever, and applied to everything already recorded from it.

**Any time you want to know your own spend:** ask Claude *"how much have I spent this month?"*

---

## Monthly routine

**Each person**, at month end: open your `ledger-<name>.csv` from the shared folder in Excel or
Google Sheets and look down the Project column. If two folders should be the same project, tell
Claude: *"the folders X and Y are both the Rawson project"*. It re-labels everything from those
folders, past included.

**Nobody needs to update a spreadsheet.** `Higgsfield Credits.xlsx` in the shared folder rebuilds
itself every time anyone's chat ends, with a tab per person and a summary of every month.

**The manager**, once everyone has done that: ask Claude for the team report. It reads every
`ledger-*.csv` in the shared folder and produces the month by project and by person, plus a `summary-YYYY-MM.csv` ready for the team Google Sheet.

---

## What's in the shared folder

| File | What it is |
|---|---|
| `ledger-<name>.csv` | One person's charges: date, model, credits, action, folder, chat, person |
| `projects.csv` | Folder → project, shared by everyone, so names stay consistent |
| `calls/` | Raw log of each generation, used to match a charge to its project. Cleans itself up after 60 days. Leave it alone. |
| `Higgsfield Credits.xlsx` | **The one spreadsheet** — rebuilt automatically after every chat. Tabs: Summary, By project, and one per person. Open it straight from Drive. |
| `summary-YYYY-MM.csv` | Optional flat export, if you want one |

The ledger stores the **folder**, never the project name. That's why renaming a project instantly
re-labels every past charge.

---

## What it can't do

- **Generations made on the Higgsfield website** aren't captured — no folder, so no project.
  Generate through Claude if you want it counted.
- **Shared Higgsfield accounts:** charges are matched to your own logged generations, so another
  person's work won't land on your ledger. In the rare case two people generate the same model in
  the same second, a charge can land on the wrong ledger; the team total stays correct.
- Credits are only readable inside a Claude chat, which is why pricing happens at the end of a
  chat rather than silently in the background.

