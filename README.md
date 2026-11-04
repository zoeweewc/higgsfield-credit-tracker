# Higgsfield Credit Tracker

Records every Higgsfield generation made through Claude — how many credits it cost, and which
project it was for — so the team can see where the month's credits went.

**The folder you work in decides the project.** Work inside the `Reeve` folder and it counts as
Reeve, including every subfolder. Nobody has to label anything while they work.

---

## Install (once, per person)

Open Claude Code and paste this, with your own first name, and the address and password Zoe
sent you:

```bash
claude plugin marketplace add zoeweewc/higgsfield-credit-tracker
claude plugin install higgsfield-credits@funnelduo-tools
```

```bash
python3 "$(ls -d ~/.claude/plugins/cache/*/higgsfield-credits/*/scripts/hf_credits.py | tail -1)" setup --user <first name> --endpoint <address> --secret <password>
```

```bash
python3 "$(ls -d ~/.claude/plugins/cache/*/higgsfield-credits/*/scripts/hf_credits.py | tail -1)" backfill --since 2026-10-01
```

Restart Claude Code. That's the whole setup. No Google Drive, no GitHub account, nothing to log
into. The last line reads your own past chats back to the date you give it, so the credits you
have already spent are counted from day one.

The address and password are private. They are never in this repository.

---

## What happens after that

**While you work:** nothing. Keep making videos as usual.

**When you finish a chat:** if that chat made any Higgsfield videos or images, Claude prices
them before the chat can end, and tells you one line like `This run: 144 credits — Reeve`.

**If you worked in a folder that has no project yet,** Claude asks once: *"What project is this
folder for?"* Answer in your own words — `Rawson`, `Nike launch`, anything. It's remembered for
that folder, and applied to everything already recorded from it.

**Any time you want to know your own spend:** ask Claude *"how much have I spent this month?"*

Your own records stay on your Mac, in `~/Higgsfield Credits`. Each new line is also sent to the
team sheet. If you're offline, or the sheet is down, nothing breaks: it sends again next time.

---

## The team sheet (for whoever runs the team)

One Google Sheet, filled in automatically. Nobody else needs access to it.

| Tab | What it is |
|---|---|
| **Summary** | Each month, by project: credits, clips made, images made, finished ads |
| **By person** | Each month, by person |
| **Month totals** | One line per month |
| **Project names** | Everyone's project names, each with an empty box. Type your own team name for a project and every total uses it. This is how `Reeve` and `Reeve Ads` from two people become one project. |
| **Ledger**, **Finished ads** | The raw lines the totals are built from. Leave these alone. |

**Credits** menu → **Refresh summary** rebuilds the totals after you edit *Project names*. It also
rebuilds by itself whenever anyone's tracker sends something.

### Setting up the sheet (once)

1. Make a new Google Sheet.
2. **Extensions → Apps Script.** Delete what's there and paste in `apps-script/Code.gs` from this
   repository. Change `REPLACE-ME` at the top to a password of your own. Everyone's tracker
   sends this along; keep it private.
3. **Deploy → New deployment → Web app.** *Execute as:* **Me**. *Who has access:* **Anyone**.
   Click **Deploy** and approve the permissions it asks for.
4. Copy the **Web app URL**. That, plus your password, is what you send each person as the
   `<address>` and `<password>` above.

"Anyone" only means anyone who has the address and the password can send a line. They cannot
read or change the sheet.

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

Plugin updates are not automatic. When the tracker changes, the version number in
`plugins/higgsfield-credits/.claude-plugin/plugin.json` is raised, and each person runs:

```bash
claude plugin marketplace update funnelduo-tools
```

```bash
claude plugin update higgsfield-credits@funnelduo-tools
```

then restarts Claude Code. **The version has to change or the update does nothing** — it reports
"already at the latest version" and keeps the old files.

Changing the sheet's script (`apps-script/Code.gs`) needs no update on anyone's Mac: paste it into
the sheet's Apps Script and deploy a new version.

---

## What it can't do

- **Generations made on the Higgsfield website** aren't captured — no folder, so no project.
  Generate through Claude if you want it counted.
- **Shared Higgsfield accounts:** charges are matched to your own logged generations, so another
  person's work won't land on your ledger. In the rare case two people generate the same model in
  the same second, a charge can land on the wrong ledger; the team total stays correct.
- Credits are only readable inside a Claude chat, which is why pricing happens at the end of a
  chat rather than silently in the background.
