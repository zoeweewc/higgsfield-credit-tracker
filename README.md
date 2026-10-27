# Higgsfield Credit Tracker

Records every Higgsfield generation made through Claude — how many credits it cost, and which
project it was for — so the team can see where the month's credits went.

**The folder you work in decides the project.** Work inside the `Reeve` folder and it counts as
Reeve, including every subfolder. Nobody has to label anything while they work.

---

## Install (once, per person)

Everything comes from one shared Google Drive folder. No GitHub account, nothing to log into.

**Step 1 — get the shared folder onto your Mac.** (You do this part yourself.)

1. Install **Google Drive for desktop** and sign in, if it isn't already.
2. In Google Drive in your browser: **Shared with me** → right-click **Higgsfield Credits**
   → **Organise** → **Add shortcut to Drive** → pick **My Drive** → **Add**.
3. Wait for Drive to finish syncing.

Don't skip the shortcut. A folder sitting only in "Shared with me" never appears on your Mac,
and the tracker won't find it.

**Step 2 — install the tracker.** Run these, in order, replacing `<your first name>`:

```bash
HF=$(echo ~/Library/CloudStorage/GoogleDrive-*/My\ Drive/Higgsfield\ Credits)
claude plugin marketplace add "$HF/Install"
claude plugin install higgsfield-credits@funnelduo-tools
```

```bash
python3 "$(ls -d ~/.claude/plugins/cache/*/higgsfield-credits/*/scripts/hf_credits.py | tail -1)" setup --user <your first name>
```

That last line also reads your own past chats from the start of this month, so the credits you have
already spent are in there from day one.

Restart Claude Code. That's the whole setup.

If it says it can't find the shared folder, step 1 isn't done or Drive hasn't finished syncing.
Wait for it, then run the line again.

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

`My Drive / Zoe Claude / Higgsfield Credits`

| File | What it is |
|---|---|
| `ledger-<name>.csv` | One person's charges: date, model, credits, action, folder, chat, person |
| `projects.csv` | Folder → project, shared by everyone, so names stay consistent |
| `calls/` | Raw log of each generation, used to match a charge to its project. Cleans itself up after 60 days. Leave it alone. |
| `Higgsfield Credits.xlsx` | **The one spreadsheet** — rebuilt automatically after every chat. Tabs: Summary (credits, clips made, and finished ads delivered), By project, and one per person. Open it straight from Drive. |
| `engine/` | The tracker's actual code. This is what everyone runs. Leave it alone. |
| `Install/` | The small launcher that points at `engine/`, for installing it. Leave it alone. |
| `summary-YYYY-MM.csv` | Optional flat export, if you want one |

The ledger stores the **folder**, never the project name. That's why renaming a project instantly
re-labels every past charge.

---

## How finished ads are counted

A **finished ad** is one video file sitting in a folder whose name contains "final" inside that
project — `Ads/Final Ads`, `Ad Production/Ads/Final`, and so on. Put the delivered cut there and
it's counted; drafts and takes elsewhere are not. `Archive`, `Old`, `Draft`, `WIP` and `Raw`
subfolders are skipped.

"Clips made" is the raw count of generations, which is usually 20-40x higher. The gap between
the two is what reruns and takes cost you.

---

## Updating it later

**Almost nothing to do.** The tracker's code lives in `engine/` in the shared Drive folder, and
that is what everyone actually runs. Fix something there and the next generation anyone makes uses
the fixed version — no commands, no restart, no message to the team.

Each person's Mac holds only a small launcher that points at `engine/`, plus a spare copy of the
code for when Drive is unreachable. The launcher is the only part a plugin update can change, and
it is deliberately tiny, so it should almost never need one.

What does and doesn't need an update:

| Change | Needs an update? |
|---|---|
| Anything in `engine/` — how spend is counted, reports, the spreadsheet | No |
| Project names (`projects.csv`) | No |
| How the hooks are wired (`hooks.json`), or what the skill tells Claude (`SKILL.md`) | Yes |

When one of those does change, refresh `Install/` in the shared folder, bump the version in
`plugin.json`, and have each person run:

```bash
claude plugin marketplace update funnelduo-tools
```

```bash
claude plugin update higgsfield-credits@funnelduo-tools
```

then restart Claude Code. **The version has to change or the update does nothing** — it reports
"already at the latest version" and keeps the old files.

Everyone with edit access to the shared folder can change code that runs on the whole team's Macs.
That is the trade for updates being free; keep the folder shared with the team only.

---

## What it can't do

- **Generations made on the Higgsfield website** aren't captured — no folder, so no project.
  Generate through Claude if you want it counted.
- **Shared Higgsfield accounts:** charges are matched to your own logged generations, so another
  person's work won't land on your ledger. In the rare case two people generate the same model in
  the same second, a charge can land on the wrong ledger; the team total stays correct.
- Credits are only readable inside a Claude chat, which is why pricing happens at the end of a
  chat rather than silently in the background.

