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

If `endpoint` and `secret` are in the config, every new charge is also sent to the team's
Google Sheet (see apps-script/Code.gs), so nobody needs access to the team's Drive.
"""
import argparse, csv, json, os, re, sys, time, urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~")
CONFIG_PATH = os.path.join(HOME, ".higgsfield-credits", "config.json")
DEFAULT_LEDGER_DIR = os.path.join(HOME, "Higgsfield Credits")
LEDGER_FIELDS = ["created_at", "model", "credits", "action", "kind", "folder", "session_id", "session_title", "user", "ad"]

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

VIDEO_MODELS = ("seedance", "kling", "wan ", "cinema", "omni", "hailuo", "veo", "sora", "runway",
                "motion control", "lipsync", "video", "multiplier")
IMAGE_MODELS = ("nano banana", "seedream", "gpt image", "soul", "flux", "midjourney", "image", "topaz")


def kind_of(tool, model_name):
    """video / image / audio - from the tool that was called, or the charge's model name."""
    t = (tool or "").lower()
    if "video" in t or t.startswith("motion") or "clipper" in t or "shorts" in t:
        return "video"
    if "image" in t or "background" in t or "outpaint" in t:
        return "image"
    if "audio" in t or "voice" in t or "dubbing" in t or "speech" in t:
        return "audio"
    m = (model_name or "").lower()
    if any(w in m for w in VIDEO_MODELS):
        return "video"
    if any(w in m for w in IMAGE_MODELS):
        return "image"
    if "voice" in m or "lipsync" in m or "audio" in m:
        return "audio"
    return "other"


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


def texts(x):
    """Every string inside a tool result, whatever shape it came in."""
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from texts(v)
    elif isinstance(x, list):
        for v in x:
            yield from texts(v)


def track_bash(event):
    """A chat just ran ad-start or finished: remember which ad THIS chat is on.

    The command can't know which chat ran it, but this hook can, so the pointer is kept per chat.
    That way two chats in one folder, each on its own ad, never overwrite each other.
    """
    command = (event.get("tool_input") or {}).get("command", "")
    if "hf_credits.py" not in command or not ("ad-start" in command or "finished" in command):
        return
    out = "\n".join(texts(event.get("tool_response")))
    sid = event.get("session_id", "")
    state = load_active()
    started = re.search(r'(?:Started|Resumed) ad "([^"]+)"', out)
    finished = re.search(r'Finished: "([^"]+)"', out)
    if started:
        state["sessions"][sid] = {"name": started.group(1), "at": now().isoformat(timespec="seconds")}
    elif finished and state["sessions"].get(sid, {}).get("name", "").lower() == finished.group(1).lower():
        # this chat is between ads now. Keep a blank entry so its next clip is not mistaken for another
        # chat's ad in the same folder, and let the next clip nudge Claude to open the next ad
        state["sessions"][sid] = {"name": "", "at": now().isoformat(timespec="seconds")}
        state["reminded"].pop(sid, None)
    else:
        return
    save_active(state)


def cmd_hook_post(cfg, p):
    """PostToolUse: a Higgsfield generation just ran. Log it with its folder."""
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    tool = event.get("tool_name", "")
    if tool == "Bash":
        return track_bash(event)
    if "higgsfield" not in tool.lower() and not SPEND_TOOL.search(tool):
        return
    if not SPEND_TOOL.search(tool):
        return
    models = re.findall(r'"model"\s*:\s*"([^"]+)"', json.dumps(event.get("tool_input", {})))
    rec = {"at": now().isoformat(timespec="seconds"), "tool": tool.split("__")[-1],
           "models": models, "folder": event.get("cwd", ""), "session_id": event.get("session_id", ""),
           "user": cfg["user"], "priced": False}
    state = load_active()
    # this chat's own ad first; the folder's ad is the fallback for chats that started before per-chat tracking
    mine = state["sessions"].get(rec["session_id"])
    if mine is not None:
        ad = mine.get("name") or None  # a chat that has used per-chat tracking never borrows the folder's ad
    else:
        ad = state["folders"].get(os.path.realpath(rec["folder"])) if rec["folder"] else None
    if ad:
        rec["ad"] = ad
    with open(call_file(p, rec["session_id"]), "a") as f:
        f.write(json.dumps(rec) + "\n")
    if not ad and rec["session_id"] not in state["reminded"]:  # once per chat, never on every clip
        state["reminded"][rec["session_id"]] = now().isoformat(timespec="seconds")
        save_active(state)
        script = os.path.abspath(__file__)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": (
            "No ad is open in this folder, so this generation's credits are not tied to any ad. If it is for an "
            "ad, ask the user for the ad's name (the client's name for it, if there is one) and its style "
            "(Animated, AI UGC, Cinematic or Hypermotion), then run: "
            f"python3 \"{script}\" ad-start --name \"<name>\" --style \"<style>\". "
            "If it is not for an ad (a test, a reference image), ignore this.")}}))


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
    # a shared Higgsfield account shows everyone's charges in `transactions`; only claim our own
    calls = [c for c in read_calls(p) if c.get("user", cfg["user"]) == cfg["user"]]
    ledger = load_ledger(p)
    seen = {(r["created_at"], r["model"], r["action"]) for r in ledger}
    used_calls, added = set(), []

    for t in sorted(charges, key=lambda t: t["created_at"]):
        key = (t["created_at"], t["display_name"], t["action"])
        if key in seen:
            continue
        when = ts(t["created_at"])
        if cfg.get("endpoint") and f"{local(when):%Y-%m}" < detail_from():
            continue  # that month is kept as a total only; adding it again would count it twice
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
                      "kind": kind_of(call.get("tool"), t["display_name"]),
                      "folder": call["folder"], "session_id": call["session_id"],
                      "session_title": call.get("session_title", ""), "user": call.get("user", cfg["user"]),
                      "ad": call.get("ad", "")})

    if added:
        write_ledger(p, ledger + added)
    tagged = [r for r in added if r.get("ad")]
    if tagged:  # the ad's running cost lives in its own record, so it survives old lines being folded away
        ads = load_ads(p, cfg["user"])
        for r in tagged:
            ad = find_ad(ads, r["folder"], r["ad"])
            if ad is None:  # opened from a different folder than the chat's: fine if the name is unique
                same = [x for x in ads if x["name"].strip().lower() == r["ad"].strip().lower()]
                ad = same[0] if len(same) == 1 else None
            if ad:
                ad["credits"] += float(r["credits"])
                if r["action"] == "spend":
                    ad["clips"] += r["kind"] == "video"
                    ad["images"] += r["kind"] == "image"
        save_ads(p, cfg["user"], ads)
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
    push_new(cfg, p)


# ---------------------------------------------------------------- team sheet

PUSH_CHUNK = 150
PUSHED_PATH = os.path.join(os.path.dirname(CONFIG_PATH), "pushed.json")


def post_json(url, payload):
    """POST JSON to the team sheet's address. Apps Script answers through a redirect, which
    urllib follows. text/plain keeps it a simple request."""
    req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "text/plain;charset=utf-8"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def push_new(cfg, p, everything=False):
    """Send what the sheet hasn't got yet. Never let a failure get in the way of the chat."""
    if not cfg.get("endpoint"):
        return
    try:
        _push(cfg, p, everything)
    except Exception as e:  # offline, wrong password, Google hiccup: it all retries next time
        print(f"(Couldn't send to the team sheet just now: {e}. It will try again next time.)")


def detail_from():
    """The sheet keeps every charge only for this month and last; older months go up as totals."""
    cur = local(now())
    y, m = (cur.year, cur.month - 1) if cur.month > 1 else (cur.year - 1, 12)
    return f"{y}-{m:02d}"


def history_path(p, cfg):
    return os.path.join(p["dir"], f"history-{cfg['user']}.csv")


def load_history(path):
    """Older months this person has already folded into totals: {(month, folder): [credits, clips, images]}."""
    out = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out[(r["month"], r["folder"])] = [float(r["credits"]), int(r["clips"]), int(r["images"])]
    return out


def fold(history, r):
    """Add one ledger line to the monthly totals."""
    h = history.setdefault((f"{local(ts(r['created_at'])):%Y-%m}", r["folder"]), [0.0, 0, 0])
    h[0] += float(r["credits"])
    if r.get("action") == "spend":
        kind = r.get("kind") or kind_of("", r["model"])
        h[1] += kind == "video"
        h[2] += kind == "image"


def compact(cfg, p, cutoff):
    """Once the sheet holds an older month as a total, this Mac keeps only the total too."""
    ledger, path = load_ledger(p), history_path(p, cfg)
    mine = lambda r: (r.get("user") or cfg["user"]) == cfg["user"]
    old = [r for r in ledger if mine(r) and f"{local(ts(r['created_at'])):%Y-%m}" < cutoff]
    if not old:
        return
    history = load_history(path)
    for r in old:
        fold(history, r)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["month", "folder", "credits", "clips", "images"])
        for (month, folder), (c, v, i) in sorted(history.items()):
            w.writerow([month, folder, f"{c:.2f}", v, i])
    drop = {id(r) for r in old}
    write_ledger(p, [r for r in ledger if id(r) not in drop])


def _push(cfg, p, everything=False):
    if not cfg.get("secret"):
        raise RuntimeError("no password in the config; run setup again with --secret")
    projects, ledger = load_projects(p), load_ledger(p)
    state = {}
    if not everything and os.path.exists(PUSHED_PATH):
        try:
            state = json.load(open(PUSHED_PATH))
        except ValueError:
            state = {}
    sent = state.setdefault("rows", {})

    cutoff = detail_from()
    by_folder = load_history(history_path(p, cfg))  # months already folded into totals on this Mac
    todo, history = [], defaultdict(lambda: [0.0, 0, 0])
    for r in ledger:
        if (r.get("user") or cfg["user"]) != cfg["user"]:
            continue  # someone else's line (an old shared-folder leftover): their own tracker sends it
        month = f"{local(ts(r['created_at'])):%Y-%m}"
        proj = project_of(r["folder"], projects)
        if month < cutoff:
            fold(by_folder, r)  # not folded yet: counted in the totals below
            continue
        key = f"{r.get('user') or cfg['user']}|{r['created_at']}|{r['model']}|{r['action']}"
        if sent.get(key) == proj:
            continue  # the sheet already has this row under this project name
        todo.append((key, proj, {"key": key, "month": month,
                                 "created_at": r["created_at"], "user": r.get("user") or cfg["user"],
                                 "model": r["model"], "credits": r["credits"],
                                 "kind": r.get("kind") or kind_of("", r["model"]),  # older rows were saved without one
                                 "project": proj, "folder": r["folder"]}))
    ads = ads_payload(p, projects, cfg["user"])

    for (month, folder), (c, v, i) in by_folder.items():
        h = history[(month, project_of(folder, projects))]
        h[0] += c
        h[1] += v
        h[2] += i
    hist = [{"month": m, "project": proj, "credits": round(c, 2), "clips": v, "images": i}
            for (m, proj), (c, v, i) in sorted(history.items())]

    def send(rows, with_ads):
        res = post_json(cfg["endpoint"], {"secret": cfg["secret"], "user": cfg["user"],
                                          "rows": rows, "ads": ads if with_ads else [], "snapshot": with_ads,
                                          "history": hist if with_ads else None, "cutoff": cutoff})
        if not res.get("ok"):
            raise RuntimeError(res.get("error", "the sheet refused it"))

    for i in range(0, len(todo), PUSH_CHUNK):
        chunk = todo[i:i + PUSH_CHUNK]
        send([row for _, _, row in chunk], False)
        for key, proj, _ in chunk:
            sent[key] = proj
        os.makedirs(os.path.dirname(PUSHED_PATH), exist_ok=True)
        json.dump(state, open(PUSHED_PATH, "w"))
    if todo or ads != state.get("ads") or hist != state.get("history") or cutoff != state.get("cutoff"):
        send([], True)
        state["ads"], state["history"], state["cutoff"] = ads, hist, cutoff
        json.dump(state, open(PUSHED_PATH, "w"))
        compact(cfg, p, cutoff)  # the sheet has the totals now, so this Mac can let the detail go
    if todo:
        print(f"Sent {len(todo)} line(s) to the team sheet.")


def cmd_push(cfg, p, a):
    if not cfg.get("endpoint"):
        sys.exit("No team sheet address set. Run setup with --endpoint and --secret.")
    try:  # not push_new: someone running this by hand wants to see what went wrong
        _push(cfg, p, everything=a.all)
    except Exception as e:
        sys.exit(f"Couldn't send to the team sheet: {e}")


# ---------------------------------------------------------------- projects / reports

# An ad is opened when it starts (`ad-start`): its name, project, style, and from then on every credit
# spent in that folder. It is closed with `finished`. One ad is one name in one folder, however many
# clips went into it, and each hook variant is its own ad.

STYLES = ("Animated", "AI UGC", "Cinematic", "Hypermotion")
AD_FIELDS = ["name", "folder", "style", "started_at", "finished_at", "credits", "clips", "images", "user"]
ACTIVE_PATH = os.path.join(os.path.dirname(CONFIG_PATH), "active.json")


def style_of(text):
    n = re.sub(r"[^a-z]", "", (text or "").lower())
    if "ugc" in n:
        return "AI UGC"
    if "anim" in n:
        return "Animated"
    if "cinem" in n:
        return "Cinematic"
    if "hyper" in n:
        return "Hypermotion"
    return None


def ads_files(p, only_user=None):
    out = []
    for fn in sorted(os.listdir(p["dir"])):
        if fn.startswith("ads-") and fn.endswith(".csv"):
            who = fn[len("ads-"):-len(".csv")]
            if only_user is None or who == only_user:
                out.append((who, os.path.join(p["dir"], fn)))
    return out


def load_ads(p, user):
    fn = os.path.join(p["dir"], f"ads-{user}.csv")
    rows = []
    if os.path.exists(fn):
        with open(fn, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                r["credits"], r["clips"], r["images"] = float(r["credits"] or 0), int(r["clips"] or 0), int(r["images"] or 0)
                rows.append(r)
    return rows


def save_ads(p, user, rows):
    with open(os.path.join(p["dir"], f"ads-{user}.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=AD_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({**r, "credits": f"{r['credits']:.2f}"})


def same_folder(a, b):
    """The same folder can be written two ways (a shortcut, a symlink); compare where they really point."""
    return a == b or os.path.realpath(a) == os.path.realpath(b)


def where():
    """The folder this command was run from, as the shell sees it."""
    return os.environ.get("PWD") or os.getcwd()


def find_ad(rows, folder, name):
    for r in rows:
        if r["name"].strip().lower() == name.strip().lower() and same_folder(r["folder"], folder):
            return r
    return None


def load_active():
    """Which ad is open in which folder, and which chats have already been nudged. Local to this Mac."""
    state = {"folders": {}, "sessions": {}, "reminded": {}}
    if os.path.exists(ACTIVE_PATH):
        try:
            state.update(json.load(open(ACTIVE_PATH)))
        except ValueError:
            pass
    return state


def save_active(state):
    cutoff = (now() - timedelta(days=30)).isoformat(timespec="seconds")
    state["reminded"] = {k: v for k, v in state["reminded"].items() if v >= cutoff}
    state["sessions"] = {k: v for k, v in state["sessions"].items() if v.get("at", "") >= cutoff}
    os.makedirs(os.path.dirname(ACTIVE_PATH), exist_ok=True)
    json.dump(state, open(ACTIVE_PATH, "w"))


def count_finished_ads(p, projects, rows=None, only_user=None):
    """Finished videos per (month finished, project)."""
    counts = defaultdict(int)
    for _, fn in ads_files(p, only_user):
        who = os.path.basename(fn)[len("ads-"):-len(".csv")]
        for ad in load_ads(p, who):
            if ad["finished_at"]:
                counts[(f"{local(ts(ad['finished_at'])):%Y-%m}", project_of(ad["folder"], projects))] += 1
    return counts


def ads_payload(p, projects, user):
    out = []
    for ad in load_ads(p, user):
        done = bool(ad["finished_at"])
        out.append({"month": f"{local(ts(ad['finished_at'] or ad['started_at'])):%Y-%m}", "name": ad["name"],
                    "project": project_of(ad["folder"], projects), "style": ad["style"],
                    "credits": round(ad["credits"], 2), "clips": ad["clips"], "images": ad["images"],
                    "status": "Finished" if done else "In progress"})
    return out


def cmd_ad_start(cfg, p, a):
    folder, name = a.folder or where(), a.name.strip()
    style = style_of(a.style) if a.style else None
    if a.style and not style:
        sys.exit(f"The style must be one of: {', '.join(STYLES)}.")
    rows = load_ads(p, cfg["user"])
    ad = find_ad(rows, folder, name)
    if ad is None:
        if not style:
            sys.exit("A new ad needs a style: " + ", ".join(STYLES) + ". Ask the user which one.")
        ad = {"name": name, "folder": folder, "style": style, "started_at": now().isoformat(timespec="seconds"),
              "finished_at": "", "credits": 0.0, "clips": 0, "images": 0, "user": cfg["user"]}
        rows.append(ad)
        verb = "Started"
    else:
        if style:
            ad["style"] = style
        verb = "Resumed"
    save_ads(p, cfg["user"], rows)
    state = load_active()
    state["folders"][os.path.realpath(folder)] = ad["name"]
    save_active(state)
    project = project_of(folder, load_projects(p))
    print(f'{verb} ad "{ad["name"]}" - {project if project != "?" else "no project yet"}, {ad["style"] or "no style yet"}. '
          f"Every credit spent in this folder now counts to it ({ad['credits']:,.2f} so far).")
    push_new(cfg, p)


def cmd_finished(cfg, p, a):
    folder, name = a.folder or where(), a.name.strip()
    style = style_of(a.style) if a.style else None
    if a.style and not style:
        sys.exit(f"The style must be one of: {', '.join(STYLES)}.")
    rows = load_ads(p, cfg["user"])
    ad = find_ad(rows, folder, name)
    if a.remove:
        if not (ad and ad["finished_at"]):
            print(f'"{name}" is not counted as finished in this folder.')
            return
        ad["finished_at"] = ""
        print(f'"{name}" is no longer counted as finished.')
    else:
        if ad is None:  # finished without ever being opened: counted, but its cost was not tracked
            ad = {"name": name, "folder": folder, "style": "", "started_at": now().isoformat(timespec="seconds"),
                  "finished_at": "", "credits": 0.0, "clips": 0, "images": 0, "user": cfg["user"]}
            rows.append(ad)
        elif ad["finished_at"]:
            print(f'"{name}" is already counted as finished. Nothing added.')
            return
        if style:
            ad["style"] = style
        ad["finished_at"] = now().isoformat(timespec="seconds")
        state = load_active()
        key = os.path.realpath(folder)
        if state["folders"].get(key, "").lower() == name.lower():
            del state["folders"][key]
            save_active(state)
        project = project_of(folder, load_projects(p))
        print(f'Finished: "{name}" - {project if project != "?" else "no project yet"}, '
              f'{ad["style"] or "no style recorded"}, {ad["credits"]:,.2f} credits.'
              + ("" if ad["credits"] or ad["style"] else " (This ad was never opened, so its cost was not tracked.)")
              + ("" if ad["style"] else " Ask the user its style and run finished again with --style."))
    save_ads(p, cfg["user"], rows)
    build_workbook(p)
    push_new(cfg, p)


def cmd_ads(cfg, p, a):
    ads = [ad for who, _ in ads_files(p) for ad in load_ads(p, who)]
    if a.month:
        ads = [ad for ad in ads if f"{local(ts(ad['finished_at'] or ad['started_at'])):%Y-%m}" == a.month]
    if not ads:
        sys.exit("No videos recorded" + (f" for {a.month}." if a.month else "."))
    projects = load_projects(p)
    done = [ad for ad in ads if ad["finished_at"]]
    print("Cost per finished video, by style" + (f" - {a.month}" if a.month else "") + "\n")
    print("| Style | Finished videos | In progress | Credits on finished videos | Credits per video |")
    print("|---|---:|---:|---:|---:|")
    for st in list(STYLES) + [""]:
        f = [ad for ad in done if ad["style"] == st]
        o = [ad for ad in ads if not ad["finished_at"] and ad["style"] == st]
        if not f and not o:
            continue
        c = sum(ad["credits"] for ad in f)
        print(f"| {st or '(no style)'} | {len(f)} | {len(o)} | {c:,.2f} | {c / len(f):,.2f} |" if f
              else f"| {st or '(no style)'} | 0 | {len(o)} | 0.00 | - |")
    print("\n| Video | Project | Style | Credits | Clips | Images | Status |\n|---|---|---|---:|---:|---:|---|")
    for ad in sorted(ads, key=lambda x: -x["credits"]):
        proj = project_of(ad["folder"], projects)
        print(f"| {ad['name']} | {proj if proj != '?' else '(needs project)'} | {ad['style'] or '-'} | "
              f"{ad['credits']:,.2f} | {ad['clips']} | {ad['images']} | "
              f"{'Finished' if ad['finished_at'] else 'In progress'} |")


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

    ads = count_finished_ads(p, projects, rows)
    summary = [["Month", "Person", "Project", "Credits", "Clips made", "Images made"]]
    agg = defaultdict(float)
    made = defaultdict(lambda: [0, 0])
    for r in rows:
        key = (r["_month"], r["user"], r["_project"])
        agg[key] += float(r["credits"])
        if r.get("action") == "spend":
            if r.get("kind") == "video":
                made[key][0] += 1
            elif r.get("kind") == "image":
                made[key][1] += 1
    for key, v in sorted(agg.items(), key=lambda kv: (kv[0][0], -kv[1])):
        m, who, proj = key
        summary.append([m, who, "(needs project)" if proj == "?" else proj, round(v, 2),
                        made[key][0], made[key][1]])
    summary += [[], ["Finished videos (files in each project's Final folder)"],
                ["Month", "Project", "Finished videos"]]
    for (m, proj), n in sorted(ads.items()):
        summary.append([m, proj, n])
    summary += [[], ["Month totals"], ["Month", "", "", "Credits", "Clips", "Images", "Finished videos"]]
    for m in months:
        keys = [k for k in agg if k[0] == m]
        summary.append([m, "", "", round(sum(agg[k] for k in keys), 2),
                        sum(made[k][0] for k in keys), sum(made[k][1] for k in keys),
                        sum(n for (mm, _), n in ads.items() if mm == m)])

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
        tab = [["Date", "Model", "Made", "Credits", "Project", "Folder"]]
        for r in sorted((r for r in rows if r["user"] == who), key=lambda r: r["created_at"], reverse=True):
            tab.append([f"{local(ts(r['created_at'])):%Y-%m-%d %H:%M}", r["model"],
                        "refund" if r.get("action") == "refund" else r.get("kind", ""),
                        round(float(r["credits"]), 2),
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
    push_new(cfg, p)


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
                if not r.get("kind"):
                    r["kind"] = kind_of("", r.get("model"))
                out.append(r)
    return out


def table(title, rows, key_name, projects):
    agg, vids, imgs = defaultdict(float), defaultdict(int), defaultdict(int)
    for r in rows:
        agg[r["_key"]] += float(r["credits"])
        if r.get("action") == "spend":  # refunds shouldn't count as something made
            if r.get("kind") == "video":
                vids[r["_key"]] += 1
            elif r.get("kind") == "image":
                imgs[r["_key"]] += 1
    total = sum(agg.values())
    print(f"{title}\n")
    finished = rows[0].get("_ads") if rows else None
    head = " Finished videos |" if finished is not None else ""
    print(f"| {key_name} | Credits | Clips made | Images made |{head} Share |")
    print("|---|---:|---:|---:|" + ("---:|" if finished is not None else "") + "---:|")
    for k, v in sorted(agg.items(), key=lambda kv: -kv[1]):
        share = f"{v / total:.0%}" if total else "-"
        extra = f" {finished.get(k, 0)} |" if finished is not None else ""
        print(f"| {k} | {v:,.2f} | {vids[k]} | {imgs[k]} |{extra} {share} |")
    extra_tot = f" **{sum(finished.values())}** |" if finished is not None else ""
    print(f"| **Total** | **{total:,.2f}** | **{sum(vids.values())}** | **{sum(imgs.values())}** |{extra_tot} |")


def cmd_report(cfg, p, a):
    projects = load_projects(p)
    rows = rows_for(p, user_files=a.team)
    month = a.month or f"{local(now()):%Y-%m}"
    rows = [r for r in rows if f"{local(ts(r['created_at'])):%Y-%m}" == month]
    if not rows:
        if cfg.get("endpoint") and month < detail_from():
            sys.exit(f"{month} is older than two months, so it is kept as monthly totals in the team sheet "
                     f"(the Summary and Month totals tabs), not here.")
        sys.exit(f"Nothing recorded for {month}.")
    for r in rows:
        r["_project"] = project_of(r["folder"], projects)
    head = datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    ads = count_finished_ads(p, projects, rows)
    by_proj_ads = {(pr if pr != "?" else "? (needs project)"): n
                   for (m, pr), n in ads.items() if m == month}
    for r in rows:
        r["_key"] = r["_project"] if r["_project"] != "?" else "? (needs project)"
        r["_ads"] = by_proj_ads
    table(f"Higgsfield spend by project - {head}" + (" - WHOLE TEAM" if a.team else ""), rows, "Project", projects)
    if a.team:
        print()
        for r in rows:
            r["_key"] = r["user"]
            r.pop("_ads", None)
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
    if a.endpoint:
        cfg["endpoint"] = a.endpoint.strip()
    if a.secret:
        cfg["secret"] = a.secret.strip()
    if a.ledger_dir:
        cfg["ledger_dir"] = os.path.expanduser(a.ledger_dir)
    elif cfg["ledger_dir"] == DEFAULT_LEDGER_DIR and not cfg.get("endpoint"):
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
    os.chmod(CONFIG_PATH, 0o600)  # holds the team sheet's password
    p = paths(cfg)
    if not os.path.exists(p["projects"]):
        save_projects(p, [])
    print(f"Name: {cfg['user']}\nRecords folder: {cfg['ledger_dir']}\nConfig: {CONFIG_PATH}\n"
          f"Team sheet: {'connected' if cfg.get('endpoint') else 'not connected'}\n")
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
    s.add_argument("--endpoint", help="the team sheet's web address"); s.add_argument("--secret", help="its password")
    s.add_argument("--no-backfill", action="store_true")
    s = sub.add_parser("backfill"); s.add_argument("--since", help="YYYY-MM-DD, default the 1st of this month")
    sub.add_parser("workbook")
    s = sub.add_parser("push"); s.add_argument("--all", action="store_true", help="resend everything, not just what's new")
    s = sub.add_parser("ad-start", help="open an ad: from now on its credits are counted")
    s.add_argument("--name", required=True); s.add_argument("--style"); s.add_argument("--folder", help="default: the folder you are in")
    s = sub.add_parser("finished", help="count one finished ad"); s.add_argument("--name", required=True)
    s.add_argument("--style"); s.add_argument("--folder", help="default: the folder you are in"); s.add_argument("--remove", action="store_true")
    s = sub.add_parser("ads", help="cost per ad and per style"); s.add_argument("--month")
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
    elif a.cmd == "push":
        cmd_push(cfg, p, a)
    elif a.cmd == "ad-start":
        cmd_ad_start(cfg, p, a)
    elif a.cmd == "finished":
        cmd_finished(cfg, p, a)
    elif a.cmd == "ads":
        cmd_ads(cfg, p, a)


if __name__ == "__main__":
    main()
