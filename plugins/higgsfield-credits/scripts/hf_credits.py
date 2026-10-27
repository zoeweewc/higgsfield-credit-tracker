#!/usr/bin/env python3
"""Launcher for the credit tracker.

The tracker's own code lives in the team's shared Drive folder, under `engine/`, so a
fix reaches everyone as soon as Drive syncs it - nobody runs an update and nobody
restarts. The copy bundled beside this file, in `_engine/`, is the fallback: it runs
before setup knows where the shared folder is, and any time the shared copy can't be
read (Drive offline, folder not shared yet, half-finished sync).

Keeping this launcher tiny matters: it is the only part that a plugin update can
change, so it should almost never need to.
"""
import json, os, sys

HOME = os.path.expanduser("~")
CONFIG_PATH = os.path.join(HOME, ".higgsfield-credits", "config.json")
BUNDLED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_engine", "hf_credits.py")
# The engine's last line. A copy without it is still syncing, so it is not safe to run.
COMPLETE = "main()"


def shared_engine():
    """The shared folder's engine, if setup has recorded that folder and the copy is whole."""
    try:
        ledger_dir = json.load(open(CONFIG_PATH))["ledger_dir"]
    except (OSError, ValueError, KeyError):
        return None
    path = os.path.join(os.path.expanduser(ledger_dir), "engine", "hf_credits.py")
    try:
        with open(path, encoding="utf-8") as f:
            body = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    if not body.startswith("#!") or body.rstrip().rsplit("\n", 1)[-1].strip() != COMPLETE:
        return None
    return path


def main():
    for engine in (shared_engine(), BUNDLED):
        if not engine:
            continue
        try:
            os.execv(sys.executable, [sys.executable, engine] + sys.argv[1:])
        except OSError:
            continue  # unreadable after all - try the next one
    sys.exit("Higgsfield credit tracker: no runnable copy found. Re-run `setup`.")


if __name__ == "__main__":
    main()
