#!/usr/bin/env python3
"""Higgsfield credit tracking: records what each generation cost and which project it was for.

Two halves:
  * The hooks call `hook-post` (logs every generation the moment it is made) and
    `hook-stop` (refuses to end a chat while generations are unpriced).
  * A person or Claude calls `sync`, `report`, `project` and `team`.

Credits are only visible through the Higgsfield MCP `transactions` tool inside a chat,
so `sync` reads that tool's results out of the chat transcript and matches each charge
to a logged call by time. The ledger stores the FOLDER, never the project name, so
renaming a project later re-labels every past charge automatically.

Files live in the ledger directory (config: ~/.higgsfield-credits/config.json):
  ledger-<user>.csv   this person's charges        (one per teammate, syncs via Drive)
  projects.csv        folder -> project, shared by the whole team
  calls/<id>.jsonl    raw call log per chat, kept for matching and auditing
"""
import argparse, csv, json, os, re, sys, time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~")
CONFIG_PATH = os.path.join(HOME, ".higgsfield-credits", "config.json")
DEFAULT_LEDGER_DIR = os.path.join(HOME, "Higgsfield Credits")
LEDGER_FIELDS = ["created_at", "model", "credits", "action", "folder", "session_id", "session_title", "user"]

# Tools that cost credits are matched by name; read-only tools never charge.
SPEND_TOOL = re.compile(r"__(generate_|upscale_|outpaint_|remove_background|reframe|voice_change|dubbing|"
                        r"motion_control|execute_preset|ads_studio_generate|ai_influencer_generate|"
                        r"shorts_studio_create|personal_clipper_create|scene_builder_3d_generate)", re.I)
# A charge lands within seconds of its call; allow generous slack for clock drift.
WINDOW_BEFORE, WINDOW_AFTER = timedelta(seconds=180), timedelta(seconds=180)


# ---------------------------------------------------------------- config / paths

def load_config():
    cfg = {"user": os.environ.get("USER", "me"), "ledger_dir": DEFAULT_LEDGER_DIR}
    if os.path.exists(CONFIG_PATH):
        try:
            cfg.update(json.load(open(CONFIG_PATH)))
        except ValueError:
            pass
    cfg["ledger_dir"] = os.path.expanduser(cfg["ledger_dir"])
    return cfg


def paths(cfg):
    d = cfg["ledger_dir"]
    os.makedirs(os.path.join(d, "calls"), exist_ok=True)
    return {"dir": d, "ledger": os.path.join(d, f"ledger-{cfg['user']}.csv"),
            "projects": os.path.join(d, "projects.csv"), "calls": os.path.join(d, "calls")}


def ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local(t):
    """UTC -> the person's own clock, so months start and end when they do."""
    return t + timedelta(seconds=-time.timezone if not time.daylight else -time.altzone)


# ---------------------------------------------------------------- projects table

def load_projects(p):
    rows = []
    if os.path.exists(p["projects"]):
        with open(p["projects"], newline="", encoding="utf-8") as f:
            rows = [(r["folder_contains"], r["project"]) for r in csv.DictReader(f) if r.get("folder_contains")]
    rows.sort(key=lambda r: -len(r[0]))  # most specific path wins
    return rows


def save_projects(p, rows):
    with open(p["projects"], "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["folder_contains", "project"])
        w.writerows(rows)


def project_of(folder, projects):
    for needle, name in projects:
        if needle and needle in folder:
            return name
    return "?"


# ---------------------------------------------------------------- call log (hooks)

def call_file(p, session_id):
    return os.path.join(p["calls"], f"{session_id or 'unknown'}.jsonl")


def read_calls(p, session_id=None):
    files = [call_file(p, session_id)] if session_id else \
            [os.path.join(p["calls"], f) for f in os.listdir(p["calls"]) if f.endswith(".jsonl")]
    out = []
    for fn in files:
        if not os.path.exists(fn):
            continue
        for line in open(fn, errors="ignore"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def cmd_hook_post(cfg, p):
    """PostToolUse: a Higgsfield generation just ran. Log it with its folder."""
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    tool = event.get("tool_name", "")
    if "higgsfield" not in tool.lower() and not SPEND_TOOL.search(tool):
        return
    if not SPEND_TOOL.search(tool):
        return
    models = re.findall(r'"model"\s*:\s*"([^"]+)"', json.dumps(event.get("tool_input", {})))
    rec = {"at": now().isoformat(timespec="seconds"), "tool": tool.split("__")[-1],
           "models": models, "folder": event.get("cwd", ""), "session_id": event.get("session_id", ""),
           "user": cfg["user"], "priced": False}
    with open(call_file(p, rec["session_id"]), "a") as f:
        f.write(json.dumps(rec) + "\n")


def cmd_hook_stop(cfg, p):
    """Stop: don't let the chat end while this chat's generations have no price yet."""
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    if event.get("stop_hook_active"):  # already asked once; never loop
        return
    session_id = event.get("session_id", "")
    pending = [c for c in read_calls(p, session_id) if not c.get("priced")]
    if not pending:
        return
    script = os.path.abspath(__file__)
    print(json.dumps({"decision": "block", "reason": (
        f"{len(pending)} Higgsfield generation(s) in this chat have not been costed yet. "
        f"Do this now, then finish:\n"
        f"1. Call the Higgsfield MCP `transactions` tool with size {min(100, len(pending) + 10)}.\n"
        f"2. Run: python3 \"{script}\" sync --transcript \"{event.get('transcript_path', '')}\"\n"
        f"3. Tell the user one line: what this run cost in credits, and which project.\n"
        f"If sync reports a folder with no project, ask the user what project that folder is, "
        f"then run: python3 \"{script}\" project \"<folder path>\" \"<project name>\"")}))


# ---------------------------------------------------------------- sync

def transactions_from_transcript(path):
    """Every `transactions` result Claude has fetched in this chat."""
    out = []
    if not path or not os.path.exists(path):
        return out
    want = set()
    for line in open(path, errors="ignore"):
        if '"tool_use"' not in line and '"tool_result"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        content = (d.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name", "").endswith("__transactions"):
                want.add(b.get("id"))
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in want:
                body = b.get("content")
                if isinstance(body, list):
                    body = "".join(x.get("text", "") for x in body if isinstance(x, dict))
                try:
                    items = json.loads(body).get("items", [])
                except (ValueError, AttributeError, TypeError):
                    continue
                out += [t for t in items if isinstance(t, dict) and
                        {"created_at", "display_name", "credits", "action"} <= t.keys()]
    return out


def load_ledger(p):
    if not os.path.exists(p["ledger"]):
        return []
    with open(p["ledger"], newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_ledger(p, rows):
    with open(p["ledger"], "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        w.writeheader()
        w.writerows(rows)


def cmd_sync(cfg, p, a):
    match_charges(cfg, p, transactions_from_transcript(a.transcript))


def match_charges(cfg, p, charges):
    """Attach each charge to the generation that caused it, then save."""
    calls = read_calls(p)
    ledger = load_ledger(p)
    seen = {(r["created_at"], r["model"], r["action"]) for r in ledger}
    used_calls, added = set(), []

    for t in sorted(charges, key=lambda t: t["created_at"]):
        key = (t["created_at"], t["display_name"], t["action"])
        if key in seen:
            continue
        when = ts(t["created_at"])
        word = (re.findall(r"[a-z]+", t["display_name"].lower()) or [""])[0]
        cands = [c for c in calls
                 if ts(c["at"]) - WINDOW_BEFORE <= when <= ts(c["at"]) + WINDOW_AFTER
                 and id(c) not in used_calls]
        if not cands:
            continue  # not ours: made on the website, or by someone else on a shared account
        cands.sort(key=lambda c: (word not in " ".join(c.get("models", [])).lower().replace("_", ""),
                                  abs((ts(c["at"]) - when).total_seconds())))
        call = cands[0]
        used_calls.add(id(call))
        call["priced"] = True
        seen.add(key)
        added.append({"created_at": t["created_at"], "model": t["display_name"],
                      "credits": f"{-float(t['credits']):.2f}", "action": t["action"],
                      "folder": call["folder"], "session_id": call["session_id"],
                      "session_title": call.get("session_title", ""), "user": call.get("user", cfg["user"])})

    if added:
        write_ledger(p, ledger + added)
    # mark priced calls so the Stop hook stops asking
    by_session = defaultdict(list)
    for c in calls:
        by_session[c.get("session_id", "")].append(c)
    for sid, recs in by_session.items():
        with open(call_file(p, sid), "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")

    total = sum(float(r["credits"]) for r in added)
    print(f"Recorded {len(added)} charge(s): {total:,.2f} credits")
    projects = load_projects(p)
    unknown = defaultdict(float)
    for r in ledger + added:
        if project_of(r["folder"], projects) == "?":
            unknown[r["folder"]] += float(r["credits"])
    if unknown:
        print("\nNEEDS PROJECT - ask the user what these folders are, then run `project <folder> <name>`:")
        for folder, c in sorted(unknown.items(), key=lambda kv: -kv[1]):
            print(f"- {folder}  ({c:,.2f} credits)")
    still = [c for c in calls if not c.get("priced")]
    if still:
        print(f"\n{len(still)} generation(s) still unpriced - fetch more `transactions` pages and sync again.")
    build_workbook(p)
    prune_calls(p)


# ---------------------------------------------------------------- projects / reports

def build_workbook(p):
    """Rewrite the single shared spreadsheet: Summary tab + one tab per person."""
    try:
        import xlsx
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import xlsx
    projects = load_projects(p)
    rows = rows_for(p, user_files=True)
    for r in rows:
        r["_project"] = project_of(r["folder"], projects)
        r["_month"] = f"{local(ts(r['created_at'])):%Y-%m}"
    months = sorted({r["_month"] for r in rows})
    people = sorted({r["user"] for r in rows})

    summary = [["Month", "Person", "Project", "Credits"]]
    agg = defaultdict(float)
    for r in rows:
        agg[(r["_month"], r["user"], r["_project"])] += float(r["credits"])
    for (m, who, proj), v in sorted(agg.items(), key=lambda kv: (kv[0][0], -kv[1])):
        summary.append([m, who, "(needs project)" if proj == "?" else proj, round(v, 2)])
    summary += [[], ["Month totals"]]
    for m in months:
        summary.append([m, "", "", round(sum(v for k, v in agg.items() if k[0] == m), 2)])

    by_project = [["Project"] + months + ["Total"]]
    names = sorted({r["_project"] for r in rows}, key=lambda n: -sum(
        float(r["credits"]) for r in rows if r["_project"] == n))
    for n in names:
        line = [("(needs project)" if n == "?" else n)]
        for m in months:
            line.append(round(sum(float(r["credits"]) for r in rows if r["_project"] == n and r["_month"] == m), 2))
        line.append(round(sum(float(r["credits"]) for r in rows if r["_project"] == n), 2))
        by_project.append(line)

    sheets = [("Summary", summary), ("By project", by_project)]
    for who in people:
        tab = [["Date", "Model", "Credits", "Project", "Folder"]]
        for r in sorted((r for r in rows if r["user"] == who), key=lambda r: r["created_at"], reverse=True):
            tab.append([f"{local(ts(r['created_at'])):%Y-%m-%d %H:%M}", r["model"], round(float(r["credits"]), 2),
                        "(needs project)" if r["_project"] == "?" else r["_project"], r["folder"]])
        sheets.append((who, tab))

    out = os.path.join(p["dir"], "Higgsfield Credits.xlsx")
    try:
        xlsx.write(out, sheets)
    except OSError as e:  # the file may be open, or Drive may be mid-sync
        print(f"(could not update the spreadsheet right now: {e})")
        return
    print(f"Spreadsheet updated: {out}")


def prune_calls(p, days=60):
    """Drop call records that are priced and older than `days` - refunds land long before that."""
    cutoff = now() - timedelta(days=days)
    removed = 0
    for name in os.listdir(p["calls"]):
        if not name.endswith(".jsonl"):
            continue
        fn = os.path.join(p["calls"], name)
        keep = []
        for line in open(fn, errors="ignore"):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("priced") and ts(rec["at"]) < cutoff:
                removed += 1
            else:
                keep.append(line.rstrip("\n"))
        if keep:
            with open(fn, "w") as out:
                out.write("\n".join(keep) + "\n")
        else:
            os.remove(fn)
    if removed:
        print(f"(tidied up {removed} finished call record(s) older than {days} days)")


def cmd_project(cfg, p, a):
    rows = [r for r in load_projects(p) if r[0] != a.folder]
    rows.append((a.folder, a.project))
    save_projects(p, rows)
    moved = sum(float(r["credits"]) for r in rows_for(p, user_files=True) if a.folder in r["folder"])
    print(f'"{a.folder}" is now project "{a.project}" '
          f"({moved:,.2f} credits re-labelled, past and future).")


def rows_for(p, user_files=False):
    cfg = load_config()
    files = [p["ledger"]]
    if user_files:
        files = [os.path.join(p["dir"], f) for f in sorted(os.listdir(p["dir"]))
                 if f.startswith("ledger-") and f.endswith(".csv")]
    out = []
    for fn in files:
        who = os.path.basename(fn)[len("ledger-"):-len(".csv")]
        if not os.path.exists(fn):
            continue
        with open(fn, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                r.setdefault("user", who)
                r["user"] = r.get("user") or who
                out.append(r)
    return out


def table(title, rows, key_name, projects):
    agg = defaultdict(float)
    for r in rows:
        agg[r["_key"]] += float(r["credits"])
    total = sum(agg.values())
    print(f"{title}\n")
    print(f"| {key_name} | Credits | Share |\n|---|---:|---:|")
    for k, v in sorted(agg.items(), key=lambda kv: -kv[1]):
        share = f"{v / total:.0%}" if total else "-"
        print(f"| {k} | {v:,.2f} | {share} |")
    print(f"| **Total** | **{total:,.2f}** | |")


def cmd_report(cfg, p, a):
    projects = load_projects(p)
    rows = rows_for(p, user_files=a.team)
    month = a.month or f"{local(now()):%Y-%m}"
    rows = [r for r in rows if f"{local(ts(r['created_at'])):%Y-%m}" == month]
    if not rows:
        sys.exit(f"Nothing recorded for {month}.")
    for r in rows:
        r["_project"] = project_of(r["folder"], projects)
    head = datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    for r in rows:
        r["_key"] = r["_project"] if r["_project"] != "?" else "? (needs project)"
    table(f"Higgsfield spend by project - {head}" + (" - WHOLE TEAM" if a.team else ""), rows, "Project", projects)
    if a.team:
        print()
        for r in rows:
            r["_key"] = r["user"]
        table(f"Higgsfield spend by person - {head}", rows, "Person", projects)
    if a.csv:
        out = os.path.join(p["dir"], f"summary-{month}.csv")
        agg = defaultdict(float)
        for r in rows:
            agg[(r["user"], r["_project"])] += float(r["credits"])
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Month", "Person", "Project", "Credits"])
            for (who, proj), v in sorted(agg.items(), key=lambda kv: -kv[1]):
                w.writerow([month, who, proj, f"{v:.2f}"])
        print(f"\nSaved {out} - ready to paste into the team Google Sheet.")


TRANSCRIPTS = os.path.join(HOME, ".claude", "projects")


def cmd_backfill(cfg, p, a):
    """On install: read this month's own Claude sessions and record what they spent.

    Finds every Higgsfield generation in the local transcripts (with the folder it ran in)
    and every `transactions` result Claude happened to fetch, then matches them.
    """
    import glob
    since = ts((a.since or f"{local(now()):%Y-%m}-01") + "T00:00:00")
    calls, charges = [], []
    for fn in glob.glob(os.path.join(TRANSCRIPTS, "**", "*.jsonl"), recursive=True):
        session_id = os.path.splitext(os.path.basename(fn))[0]
        want = set()
        for line in open(fn, errors="ignore"):
            if '"tool_use"' not in line and '"tool_result"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            content = (d.get("message") or {}).get("content")
            if not isinstance(content, list) or "timestamp" not in d:
                continue
            when = ts(d["timestamp"])
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    name = b.get("name", "")
                    if name.endswith("__transactions"):
                        want.add(b.get("id"))
                    elif SPEND_TOOL.search(name) and when >= since:
                        calls.append({"at": d["timestamp"], "tool": name.split("__")[-1],
                                      "models": re.findall(r'"model"\s*:\s*"([^"]+)"', json.dumps(b.get("input", {}))),
                                      "folder": d.get("cwd", ""), "session_id": session_id,
                                      "user": cfg["user"], "priced": False})
                elif b.get("type") == "tool_result" and b.get("tool_use_id") in want:
                    body = b.get("content")
                    if isinstance(body, list):
                        body = "".join(x.get("text", "") for x in body if isinstance(x, dict))
                    try:
                        charges += [t for t in json.loads(body).get("items", [])
                                    if isinstance(t, dict) and {"created_at", "display_name", "credits", "action"} <= t.keys()]
                    except (ValueError, AttributeError, TypeError):
                        pass
    # drop duplicates and anything older than the start of the month
    seen_c = set()
    charges = [t for t in charges
               if ts(t["created_at"]) >= since
               and not ((t["created_at"], t["display_name"], t["action"]) in seen_c
                        or seen_c.add((t["created_at"], t["display_name"], t["action"])))]
    by_session = defaultdict(list)
    for c in calls:
        by_session[c["session_id"]].append(c)
    for sid, recs in by_session.items():
        existing = {(r["at"], r["folder"]) for r in read_calls(p, sid)}
        with open(call_file(p, sid), "a") as f:
            for r in recs:
                if (r["at"], r["folder"]) not in existing:
                    f.write(json.dumps(r) + "\n")
    print(f"Found {len(calls)} generation(s) and {len(charges)} charge(s) since {local(since):%d %b %Y}.")
    match_charges(cfg, p, charges)


def find_shared_folder():
    """Look for the team's shared 'Higgsfield Credits' folder in any signed-in Drive account."""
    import glob
    hits = []
    for pattern in ("Library/CloudStorage/GoogleDrive-*/My Drive/Higgsfield Credits",
                    "Library/CloudStorage/GoogleDrive-*/My Drive/*/Higgsfield Credits",
                    "Library/CloudStorage/GoogleDrive-*/My Drive/*/*/Higgsfield Credits",
                    "Library/CloudStorage/GoogleDrive-*/Shared drives/*/Higgsfield Credits",
                    "Library/CloudStorage/GoogleDrive-*/Shared drives/*/*/Higgsfield Credits",
                    "Google Drive/My Drive/Higgsfield Credits",
                    "Google Drive/My Drive/*/Higgsfield Credits"):
        hits += glob.glob(os.path.join(HOME, pattern))
    return sorted(set(hits))


def cmd_setup(cfg, p, a):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    cfg["user"] = (a.user or cfg["user"]).strip().lower().split()[0]
    if a.ledger_dir:
        cfg["ledger_dir"] = os.path.expanduser(a.ledger_dir)
    elif cfg["ledger_dir"] == DEFAULT_LEDGER_DIR:
        found = find_shared_folder()
        if len(found) == 1:
            cfg["ledger_dir"] = found[0]
        elif len(found) > 1:
            sys.exit("Found more than one shared folder. Re-run with --ledger-dir \"<the right one>\":\n  "
                     + "\n  ".join(found))
        else:
            sys.exit("Can't find the team's shared 'Higgsfield Credits' folder in Google Drive.\n"
                     "Make sure the folder has been shared with you and Google Drive for desktop has\n"
                     "finished syncing, then run this again. Or pass --ledger-dir \"<path to it>\".")
    json.dump(cfg, open(CONFIG_PATH, "w"), indent=2)
    p = paths(cfg)
    if not os.path.exists(p["projects"]):
        save_projects(p, [])
    print(f"Name: {cfg['user']}\nRecords folder: {cfg['ledger_dir']}\nConfig: {CONFIG_PATH}\n")
    if not a.no_backfill:
        print("Reading this month's past chats...")
        cmd_backfill(cfg, p, argparse.Namespace(since=None))


def main():
    ap = argparse.ArgumentParser(description="Higgsfield credit tracking")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hook-post")
    sub.add_parser("hook-stop")
    s = sub.add_parser("sync"); s.add_argument("--transcript", default="")
    s = sub.add_parser("project"); s.add_argument("folder"); s.add_argument("project")
    s = sub.add_parser("report")
    s.add_argument("--month"); s.add_argument("--team", action="store_true"); s.add_argument("--csv", action="store_true")
    s = sub.add_parser("setup"); s.add_argument("--user"); s.add_argument("--ledger-dir")
    s.add_argument("--no-backfill", action="store_true")
    s = sub.add_parser("backfill"); s.add_argument("--since", help="YYYY-MM-DD, default the 1st of this month")
    sub.add_parser("workbook")
    a = ap.parse_args()

    cfg = load_config()
    p = paths(cfg)
    if a.cmd == "hook-post":
        cmd_hook_post(cfg, p)
    elif a.cmd == "hook-stop":
        cmd_hook_stop(cfg, p)
    elif a.cmd == "sync":
        cmd_sync(cfg, p, a)
    elif a.cmd == "project":
        cmd_project(cfg, p, a)
    elif a.cmd == "report":
        cmd_report(cfg, p, a)
    elif a.cmd == "setup":
        cmd_setup(cfg, p, a)
    elif a.cmd == "backfill":
        cmd_backfill(cfg, p, a)
    elif a.cmd == "workbook":
        build_workbook(p)


if __name__ == "__main__":
    main()
