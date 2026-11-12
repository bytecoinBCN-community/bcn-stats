#!/usr/bin/env python3
"""Collects public Bytecoin network figures into stats.json and publishes them with git.

Chain figures are refreshed on every run, the X3 block share and the latest code commit hourly,
the node scan every 6 hours.
Nothing is published while the node is behind or has no peers, so the page shows stale data as stale.
"""
import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

RPC_URL = "http://127.0.0.1:8081/json_rpc"
STATE_DIR = os.environ.get("BCN_STATS_DIR", "/var/lib/bcn-stats")
REPO = os.path.join(STATE_DIR, "repo")
KNOWN_FILE = os.path.join(STATE_DIR, "known-addresses.txt")
HERE = os.path.dirname(os.path.abspath(__file__))
SHARE_EVERY = 3600
SCAN_EVERY = 6 * 3600
SHARE_WINDOW = 720
TIP_TOLERANCE = 10  # blocks behind the tip that still count as "at the top of the chain"
MAX_SUPPLY = 2 ** 64 - 1
BLOCK_TARGET = 120
COMMIT_EVERY = 3600
GITHUB_OWNER = "bytecoinBCN-community"
# Code repositories with their release branches. bcn-stats itself is left out: its commits are just new figures.
CODE_REPOS = [("bytecoin", "ubuntu-24.04"), ("bcn-stratum", "main"), ("bcn-crawl", "main"),
              ("amethyst-web-wallet", "main"), ("bcn-explorer", "main"), ("bcn-donate", "main")]

# Never published: our own home node. Set it in the service environment, not in this public file.
EXCLUDE = set(filter(None, os.environ.get("BCN_STATS_EXCLUDE", "").split(",")))
# Where the miner runs elsewhere, it sends its block share here (see --share-json); otherwise it is read from
# the local bcn-stratum journal.
SHARE_FILE = os.environ.get("BCN_SHARE_FILE", "")
SHARE_MAX_AGE = 3 * 3600

LOCATIONS = {
    "45.77.142.241": "Germany, Frankfurt",
    "31.220.87.232": "France, Lauterbourg",
    "195.154.106.27": "France, Paris",
    "78.27.242.81": "Poland",
    "135.181.141.137": "Finland, Helsinki",
    "198.27.69.208": "Canada, Montreal",
    "54.39.84.66": "Canada, Beauharnois",
    "149.28.44.51": "USA, New Jersey",
    "64.31.61.150": "USA, Los Angeles",
    "68.168.222.220": "USA, New Jersey",
    "66.42.62.115": "Singapore",
}
SEEDS = [ip + ":8080" for ip in LOCATIONS] + ["108.61.174.232:8080", "207.246.127.160:8080", "51.83.67.8:8080"]


def rpc(method, params=None):
    body = json.dumps({"jsonrpc": "2.0", "id": "0", "method": method, "params": params or {}}).encode()
    with urllib.request.urlopen(urllib.request.Request(RPC_URL, body), timeout=30) as r:
        return json.load(r)["result"]


def header(height):
    """The legacy call counts from 1, so height h is asked for as h + 1. The answer is checked."""
    h = rpc("getblockheaderbyheight", {"height": height + 1})["block_header"]
    if h["height"] != height:
        raise RuntimeError("asked for block %d, got %d" % (height, h["height"]))
    return h


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def age(stamp):
    try:
        return time.time() - datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return float("inf")


def block_share(top):
    """Share of the latest blocks that are ours and really in the main chain."""
    out = subprocess.run(["journalctl", "-u", "bcn-stratum", "--since", "8 days ago", "--no-pager", "-o", "cat"],
                         capture_output=True, text=True, timeout=120, check=True).stdout
    accepted = {}
    for m in re.finditer(r"BLOCK ACCEPTED height=(\d+) hash=(\w+)", out):
        accepted.setdefault(int(m[1]), set()).add(m[2])
    ours = sum(header(h)["hash"] in accepted.get(h, ()) for h in range(top - SHARE_WINDOW + 1, top + 1))
    return {"blocks": ours, "window": SHARE_WINDOW, "percent": round(100 * ours / SHARE_WINDOW, 1), "at": now()}


def last_commit():
    """Newest commit across our code repositories, read from the public GitHub API (one request per repo)."""
    best = None
    for repo, branch in CODE_REPOS:
        url = "https://api.github.com/repos/%s/%s/commits/%s" % (GITHUB_OWNER, repo, branch)
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "bcn-stats"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                c = json.load(r)
            date = c["commit"]["committer"]["date"]
            link = c["html_url"]
        except (OSError, ValueError, KeyError, TypeError) as ex:
            print("commit of %s not read: %s" % (repo, ex), file=sys.stderr)
            continue
        if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", date or "") or \
                not str(link).startswith("https://github.com/%s/%s/commit/" % (GITHUB_OWNER, repo)):
            continue
        if best is None or date > best["date"]:
            best = {"date": date, "repo": repo, "url": link}
    if best is None:
        raise RuntimeError("no commit could be read")
    best["at"] = now()
    return best


def valid_share(s):
    """True for {"blocks", "window", "percent", "at"} as block_share() makes it."""
    return (isinstance(s, dict) and set(s) == {"blocks", "window", "percent", "at"}
            and isinstance(s["blocks"], int) and isinstance(s["window"], int) and 0 <= s["blocks"] <= s["window"] <= 10000
            and isinstance(s["percent"], (int, float)) and 0 <= s["percent"] <= 100
            and isinstance(s["at"], str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", s["at"]) is not None)


def received_share():
    """The share the miner's host sent, if it is valid and recent, else None."""
    try:
        with open(SHARE_FILE) as f:
            s = json.load(f)
    except (OSError, ValueError):
        return None
    return s if valid_share(s) and age(s["at"]) < SHARE_MAX_AGE else None


def public_address(addr):
    host, _, port = addr.rpartition(":")
    try:
        return ipaddress.ip_address(host).is_global and port.isdigit() and host not in EXCLUDE
    except ValueError:
        return False


def scan(top):
    known = list(SEEDS)
    if os.path.exists(KNOWN_FILE):
        known += [a.strip() for a in open(KNOWN_FILE) if a.strip()]
    known = sorted(set(a for a in known if public_address(a)))
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("\n".join(known) + "\n")
    try:
        p = subprocess.run([sys.executable, "-I", os.path.join(HERE, "bcn_crawl.py"), f.name],
                           capture_output=True, text=True, timeout=600, check=True)
    finally:
        os.unlink(f.name)
    live = [json.loads(line) for line in p.stdout.splitlines() if line.startswith("{")]
    live = [n for n in live if public_address(n.get("address", ""))]
    full = [n for n in live if n.get("height", 0) > 0 and abs(n["height"] - top) <= TIP_TOLERANCE]
    tmp = KNOWN_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(sorted(set(known) | {n["address"] for n in live})) + "\n")
    os.replace(tmp, KNOWN_FILE)
    m = re.search(r"probed=(\d+)", p.stderr)
    return {
        "probed": int(m[1]) if m else len(known),
        "at": now(),
        "full": [{"address": n["address"], "height": n["height"],
                  "location": LOCATIONS.get(n["address"].rpartition(":")[0], "Unknown")}
                 for n in sorted(full, key=lambda n: n["address"])],
    }


def publish(text):
    path = os.path.join(REPO, "stats.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)
    git = ["git", "-C", REPO]
    subprocess.run(git + ["add", "stats.json"], check=True, timeout=60)
    if subprocess.run(git + ["diff", "--cached", "--quiet"], timeout=60).returncode == 0:
        return
    subprocess.run(git + ["commit", "-q", "-m", "Update network stats"], check=True, timeout=60)
    subprocess.run(git + ["push", "-q", "origin", "main"], check=True, timeout=120)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print the result, publish nothing")
    ap.add_argument("--force", action="store_true", help="refresh the share and the node scan now")
    ap.add_argument("--share-json", action="store_true",
                    help="only print our block share as JSON, for sending to the host that publishes")
    args = ap.parse_args()

    st = rpc("get_status")
    top = st["top_block_height"]
    if args.share_json:
        if top + 1 < st["top_known_block_height"] or st["outgoing_peer_count"] == 0:
            print("node not synced, no share", file=sys.stderr)
            return 1
        print(json.dumps(block_share(top)))
        return 0
    if top + 1 < st["top_known_block_height"] or st["outgoing_peer_count"] == 0:
        print("node not synced (height %d of %d, %d peers), nothing published"
              % (top, st["top_known_block_height"], st["outgoing_peer_count"]))
        return 0

    prev = {}
    try:
        prev = json.load(open(os.path.join(REPO, "stats.json")))
    except (OSError, ValueError):
        pass

    head = header(top)
    stats = {
        "schema": 1,
        "updated": now(),
        "height": top,
        "difficulty": st["top_block_difficulty"],
        "hashrate_khs": round(st["top_block_difficulty"] / BLOCK_TARGET / 1000),
        "supply_percent": round(100 * head["already_generated_coins"] / MAX_SUPPLY, 4),
        "our_share": prev.get("our_share"),
        "nodes": prev.get("nodes"),
        "last_commit": prev.get("last_commit"),
    }
    if args.force or age((prev.get("last_commit") or {}).get("at")) >= COMMIT_EVERY:
        try:
            stats["last_commit"] = last_commit()
        except RuntimeError as ex:
            print("last commit not refreshed, keeping the previous one: %s" % ex, file=sys.stderr)
    if SHARE_FILE:
        stats["our_share"] = received_share() or prev.get("our_share")
    elif args.force or age((prev.get("our_share") or {}).get("at")) >= SHARE_EVERY:
        stats["our_share"] = block_share(top)
    if args.force or age((prev.get("nodes") or {}).get("at")) >= SCAN_EVERY:
        try:
            stats["nodes"] = scan(top)
        except (subprocess.SubprocessError, OSError, ValueError) as ex:
            print("node scan failed, keeping the previous result: %s" % ex, file=sys.stderr)

    text = json.dumps(stats, indent=1) + "\n"
    if args.dry_run:
        print(text)
        return 0
    publish(text)
    print("published height %d" % top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
