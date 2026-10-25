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
USD_PER_CREDIT = 81 / 2000  # 2,000 credits = USD 81
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
        f"3. Tell the user one line: what this run cost in credits and USD, and which project.\n"
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
    charges = transactions_from_transcript(a.transcript)
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
    print(f"Recorded {len(added)} charge(s): {total:,.2f} credits (USD {total * USD_PER_CREDIT:,.2f})")
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


# ---------------------------------------------------------------- projects / reports

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
    print(f"| {key_name} | Credits | USD | Share |\n|---|---:|---:|---:|")
    for k, v in sorted(agg.items(), key=lambda kv: -kv[1]):
        share = f"{v / total:.0%}" if total else "-"
        print(f"| {k} | {v:,.2f} | ${v * USD_PER_CREDIT:,.2f} | {share} |")
    print(f"| **Total** | **{total:,.2f}** | **${total * USD_PER_CREDIT:,.2f}** | |")


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
            w.writerow(["Month", "Person", "Project", "Credits", "USD"])
            for (who, proj), v in sorted(agg.items(), key=lambda kv: -kv[1]):
                w.writerow([month, who, proj, f"{v:.2f}", f"{v * USD_PER_CREDIT:.2f}"])
        print(f"\nSaved {out} - ready to paste into the team Google Sheet.")


def cmd_setup(cfg, p, a):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    cfg["user"] = a.user or cfg["user"]
    if a.ledger_dir:
        cfg["ledger_dir"] = a.ledger_dir
    json.dump(cfg, open(CONFIG_PATH, "w"), indent=2)
    p = paths(cfg)
    if not os.path.exists(p["projects"]):
        save_projects(p, [])
    print(f"Name: {cfg['user']}\nRecords folder: {cfg['ledger_dir']}\nConfig: {CONFIG_PATH}")


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


if __name__ == "__main__":
    main()
