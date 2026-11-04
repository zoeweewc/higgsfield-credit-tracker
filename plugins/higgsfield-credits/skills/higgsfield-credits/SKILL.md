---
name: higgsfield-credits
description: Record and report Higgsfield credit spend per project, and track what each ad costs. Use whenever Higgsfield MCP generations have just run and need costing, when the Stop hook asks for a sync, when a hook says no ad is open, when the user starts a new ad or video, when the user asks what they or the team have spent or what an ad or a style costs, when the user says which project a folder belongs to, or when the user asks to compile, assemble, export or finalise a video for use, or says an IG BOMB video has been posted.
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
many **finished ads** were counted. Give it in chat, plus one line on what stands out. Don't save
reports as files unless asked.

## Tracking what each ad costs

An ad is opened when it **starts**. From then on every credit spent in that folder is counted to it
automatically, retries and rejected takes included. Credits spent before an ad was opened are not
worked out backwards.

**Start every ad before its first clip.** Run, from the folder this chat is working in:

`python3 <script> ad-start --name "<ad name>" --style "<style>"`

- **Name:** whatever the user or the client calls the ad. Don't invent one. If you don't know it, ask.
- **Style:** one of **Animated, AI UGC, Cinematic, Hypermotion**. Pick the closest from what is being
  made. If you are not sure, ask the user in one line. Never leave it blank.
- **Project** comes from the folder. Don't ask for it.
- **Each hook variant is its own ad**, with its own name and its own `ad-start`.
- **Whenever you switch to a different ad, run `ad-start` for it before its next clip, including going
  back to an earlier ad in the same chat.** The tracker remembers one current ad per chat, so the
  command is what tells it which ad each clip is for. Other chats in the same folder don't affect yours.
- **Continuing an ad in a new chat:** run `ad-start --name "<same name>"` (no style needed) and the
  cost carries on from where it was.
- If a hook says *no ad is open*, and the generation is for an ad, ask for the name and style and run
  `ad-start`. If it is a test or a reference image, ignore it.

**Finish it** when it exists as a finished ad:

`python3 <script> finished --name "<ad name>"`

- **Whenever the user asks you to compile, assemble, export or finalise a video for use.** Run it once
  the compile is done.
- **IG BOMB:** finished means **posted**. Run it when the user says it has been posted, not when it is
  compiled.
- **Every other project:** use your judgement. A cut meant to be used or delivered is finished. Drafts,
  tests, previews and a re-export of an ad already finished are not. If you can't tell, ask in one line.
- If the ad was never opened with `ad-start`, finishing it still counts it but its cost was not
  tracked, and the command says so. Add `--style "<style>"` if it has none.
- If something was marked finished by mistake: `finished --name "<name>" --remove`.

Tell the user in one line what you started or finished and what it has cost so far.

**To answer "what does an ad cost" or "which style is worth it":** run `python3 <script> ads`
(add `--month 2026-11` for one month). It shows credits per finished ad by style, and every ad with
its cost. Give the table in chat plus one line on what stands out, such as the cheapest and the most
expensive style.

## Renaming or merging projects

When the user says two folders are the same project, or renames one, just run `project` again
for each folder with the new name. Project names are resolved fresh on every report, so old
charges follow the new name automatically. Nothing is rewritten by hand.

## What is not captured

Generations made on the Higgsfield website instead of through Claude have no folder, so they
can't be assigned to a project. If a charge can't be matched to a logged call, it is left out
of the ledger rather than guessed at. Say so if the user asks why a number looks low.
