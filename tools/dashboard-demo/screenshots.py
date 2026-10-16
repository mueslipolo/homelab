"""Screenshots of the dashboard with made-up data, for the docs.

Run through screenshots.sh, which starts it in Playwright's image. It copies
stacks/dashboard/html to a scratch folder, writes mock local/*.json there,
serves it as dashboard.home.example.com with the clock frozen at NOW, and
saves one PNG per tab to docs/images/.
"""

import json
import random
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "images"
TZ = "Europe/Zurich"
NOW = datetime(2026, 10, 14, 8, 30, tzinfo=timezone(timedelta(hours=2)))
T = int(NOW.timestamp())
H, D = 3600, 86400
KIB, GIB, TIB = 1024, 1024**3, 1024**4
rnd = random.Random(7)


def at(days=0, hours=0, minutes=0):
    return T - days * D - hours * H - minutes * 60


def evening(days_ago, hh, mm):
    """Epoch of hh:mm local, `days_ago` days before NOW's date."""
    d = NOW.replace(hour=hh, minute=mm, second=0) - timedelta(days=days_ago)
    return int(d.timestamp())


def status():
    used, total = 6.9 * TIB, 10.5 * TIB
    commit = "4f2c9e1"
    return {
        "generated": at(minutes=6),
        "volume": {
            "path": "/volume1",
            "size_kb": int(total / KIB),
            "avail_kb": int((total - used) / KIB),
            "used_pct": round(used / total * 100),
        },
        "backups": [
            {"task": "Off-site", "result": "success", "at": evening(1, 21, 52), "message": ""},
            {"task": "USB disk", "result": "success", "at": evening(0, 3, 41), "message": ""},
        ],
        "dumps": [
            {"name": "Forgejo dump", "dir": "/volume1/forgejo/dumps", "file": "forgejo-dump-20261013-2000.zip", "latest": evening(1, 20, 1)},
            {"name": "Mealie dump", "dir": "/volume1/mealie/dumps", "file": "mealie-backup-20261013-200512.zip", "latest": evening(1, 20, 5)},
        ],
        "jobs": [
            {"name": "forgejo-dump", "exit": 0, "ended": evening(1, 20, 1), "message": ""},
            {"name": "mealie-backup", "exit": 0, "ended": evening(1, 20, 5), "message": ""},
            {"name": "paperless-export", "exit": 0, "ended": evening(1, 20, 14), "message": ""},
            {"name": "docker-prune", "exit": 0, "ended": evening(0, 4, 31), "message": ""},
            {"name": "storage-scan", "exit": 0, "ended": evening(0, 5, 22), "message": ""},
            {"name": "build-images", "exit": 0, "ended": at(minutes=6), "message": ""},
        ],
        "devices": {
            "configured": True,
            "devices": [
                {"owner": "anna", "kind": "phone", "name": "Pixel 8", "max_days": 7, "last_seen": None,
                 "newest_photo": at(hours=3), "online": False, "version": ""},
                {"owner": "anna", "kind": "computer", "name": "anna-laptop", "max_days": 21, "last_seen": at(minutes=20),
                 "newest_photo": None, "online": True, "version": "3.5.2"},
                {"owner": "bob", "kind": "phone", "name": "iPhone 15", "max_days": 7, "last_seen": None,
                 "newest_photo": at(days=1, hours=5), "online": False, "version": ""},
                {"owner": "bob", "kind": "computer", "name": "bob-desktop", "max_days": 21, "last_seen": at(days=12),
                 "newest_photo": None, "online": False, "version": "3.5.2"},
            ],
            "unlisted": [],
        },
        "protection": {
            "shares": [
                {"name": "homes", "path": "/volume1/homes", "snapshot": {"enabled": True, "schedule": "daily 01:00", "latest": evening(0, 1, 0), "count": 30}},
                {"name": "paperless", "path": "/volume1/paperless", "snapshot": {"enabled": True, "schedule": "daily 01:00", "latest": evening(0, 1, 0), "count": 30}},
                {"name": "forgejo", "path": "/volume1/forgejo", "snapshot": None},
                {"name": "mealie", "path": "/volume1/mealie", "snapshot": None},
                {"name": "docker", "path": "/volume1/docker", "snapshot": None},
                {"name": "media", "path": "/volume1/media", "snapshot": None},
            ],
            "tasks": [
                {"name": "Off-site", "dest": "s3", "target": "", "encrypted": True, "enabled": True, "schedule": "daily 20:40",
                 "folders": ["/homes", "/paperless", "/forgejo/dumps", "/mealie/dumps", "/docker/authelia/secrets"]},
                {"name": "USB disk", "dest": "usb", "target": "", "encrypted": False, "enabled": True, "schedule": "daily 03:00",
                 "folders": ["/homes", "/paperless", "/forgejo/dumps", "/mealie/dumps", "/docker/authelia/secrets"]},
            ],
        },
        "stacks": [
            {"name": n, "id": i, "active": True, "git": g, "current": True if g else None,
             "deployed": {"commit": commit, "at": at(days=1, hours=3)} if g else None,
             "code": {"commit": commit, "at": at(days=1, hours=3)} if g else None}
            for i, (n, g) in enumerate([("authelia", True), ("dashboard", True), ("edge", True), ("forgejo", False),
                                        ("forgejo-runner", True), ("mealie", True), ("mediarr", True),
                                        ("paperless", True), ("paperless-tarmed", True)], 1)
        ],
        "images": [
            {"stack": "paperless-tarmed",
             "code": {"commit": commit, "at": at(days=1, hours=3)},
             "built": {"commit": commit, "at": at(days=1, hours=2, minutes=50), "from_current_code": True},
             "running": {"state": "running", "commit": commit, "started": at(days=1, hours=2, minutes=40), "same_image": True}},
        ],
        "scheduler": {"sha": commit, "pull_ok": True, "pull_error": ""},
    }


def storage():
    total, used = 10.5 * TIB, 6.9 * TIB
    days = 90
    daily, v = [], used - 410 * GIB
    for i in range(days, -1, -1):
        day = (NOW - timedelta(days=i)).strftime("%Y-%m-%d")
        v += rnd.uniform(1, 9) * GIB
        if i in (61, 23):  # two clean-ups
            v -= 180 * GIB if i == 61 else 95 * GIB
        daily.append([day, v])
    scale = used / daily[-1][1]
    daily = [[d, int(b * scale)] for d, b in daily]

    def weekly(now_b, growth):
        return [int(now_b - growth * (11 - w) * GIB * rnd.uniform(0.8, 1.2)) for w in range(12)]

    shares = [
        ("media", 4.6 * TIB, 38, 160, 14), ("homes", 1.1 * TIB, 4, 21, 2), ("paperless", 96 * GIB, 0.4, 1.8, 0.1),
        ("docker", 71 * GIB, -2.5, 3.1, 0.2), ("forgejo", 3.2 * GIB, 0.1, 0.3, 0.02), ("mealie", 1.4 * GIB, 0.02, 0.1, 0.01),
    ]
    media = {
        "series": {"bytes": int(2.9 * TIB), "count": 64, "top": [
            {"name": "The Expanse", "bytes": int(312 * GIB), "episodes": 62, "gb_per_hour": 5.6, "hevc": False, "saving": int(160 * GIB), "remux": False},
            {"name": "Battlestar Galactica", "bytes": int(241 * GIB), "episodes": 75, "gb_per_hour": 3.8, "hevc": False, "saving": int(110 * GIB), "remux": False},
            {"name": "Severance", "bytes": int(118 * GIB), "episodes": 19, "gb_per_hour": 7.1, "hevc": True, "saving": 0, "remux": False},
            {"name": "Parks and Recreation", "bytes": int(96 * GIB), "episodes": 125, "gb_per_hour": 1.6, "hevc": False, "saving": int(41 * GIB), "remux": False},
            {"name": "Planet Earth II", "bytes": int(88 * GIB), "episodes": 6, "gb_per_hour": 14.2, "hevc": False, "saving": int(52 * GIB), "remux": True},
        ]},
        "film": {"bytes": int(1.6 * TIB), "count": 212, "top": [
            {"name": "Dune: Part Two (2024)", "bytes": int(64 * GIB), "gb_per_hour": 23.4, "hevc": True, "saving": 0, "remux": True},
            {"name": "Oppenheimer (2023)", "bytes": int(58 * GIB), "gb_per_hour": 19.3, "hevc": False, "saving": int(36 * GIB), "remux": True},
            {"name": "Blade Runner 2049 (2017)", "bytes": int(41 * GIB), "gb_per_hour": 15.0, "hevc": False, "saving": int(24 * GIB), "remux": False},
            {"name": "Interstellar (2014)", "bytes": int(33 * GIB), "gb_per_hour": 11.8, "hevc": True, "saving": 0, "remux": False},
            {"name": "Arrival (2016)", "bytes": int(18 * GIB), "gb_per_hour": 9.4, "hevc": False, "saving": int(9 * GIB), "remux": False},
        ]},
    }
    parts_bytes = [("media", 4.6 * TIB), ("homes", 1.1 * TIB), ("Drive version history", 310 * GIB),
                   ("paperless", 96 * GIB), ("docker", 71 * GIB), ("Snapshots", 64 * GIB), ("Hyper Backup cache", 22 * GIB)]
    kinds = {"Drive version history": "drive", "Snapshots": "snapshots", "Hyper Backup cache": "hbcache"}
    parts = [{"name": n, "kind": kinds.get(n, "share"), "bytes": int(b)} for n, b in parts_bytes]
    other = int(used - sum(p["bytes"] for p in parts))
    since = (NOW - timedelta(days=30)).strftime("%Y-%m-%d")
    delta = daily[-1][1] - daily[-31][1]
    return {
        "generated": evening(0, 5, 22),
        "volume": {"used": int(used), "total": int(total), "free": int(total - used)},
        "parts": parts,
        "other": max(other, 0),
        "counted_twice": int(48 * GIB),
        "growth": {"delta": int(delta), "since": since, "per_day": int(5.4 * GIB)},
        "history_since": daily[0][0],
        "daily": daily,
        "changes": [
            {"delta": int(118 * GIB), "kind": "films", "name": "", "new": 9},
            {"delta": int(46 * GIB), "kind": "series", "name": "The Expanse", "new": 12},
            {"delta": int(19 * GIB), "kind": "share", "name": "homes", "new": 0},
            {"delta": int(-95 * GIB), "kind": "series", "name": "Lost", "new": 0},
            {"delta": int(-6 * GIB), "kind": "share", "name": "docker", "new": 0},
        ],
        "shares": [{"name": n, "bytes": int(b), "d7": int(d7 * GIB), "d30": int(d30 * GIB), "weekly": weekly(b, g)}
                   for n, b, d7, d30, g in shares],
        "media": media,
        "reclaim": {"reencode": int(420 * GIB), "drive": int(310 * GIB), "snapshots": None,
                    "docker": int(6 * GIB), "recycle": int(3 * GIB)},
    }


DEVICES = {
    "tagline": "nas · Synology DS920+",
    "devices": [
        {"name": "Synology DS920+", "desc": "NAS", "url": "https://192.0.2.10:5001", "label": "192.0.2.10:5001"},
        {"name": "Router", "desc": "Gateway, DHCP, LAN DNS", "url": "http://192.0.2.1/", "label": "192.0.2.1"},
        {"name": "WiFi AP", "desc": "Access point", "url": "http://192.0.2.2/", "label": "192.0.2.2"},
    ],
    "diagram": {
        "isp": {"label": "ISP router", "sub": "198.51.100.1"},
        "router": {"label": "Router", "sub": "192.0.2.1"},
        "ap": {"label": "WiFi AP", "sub": "192.0.2.2"},
        "nas": {"label": "Synology DS920+", "sub": "192.0.2.10"},
        "host": {"sub": ".10"},
        "caddy": {"sub": ".20"},
    },
}

SHOTS = [("services", ""), ("storage", "#storage"), ("backups", "#backups"), ("system", "#system")]


def main():
    scheme = sys.argv[1] if len(sys.argv) > 1 else "light"
    site = Path(tempfile.mkdtemp()) / "html"
    shutil.copytree(REPO / "stacks" / "dashboard" / "html", site)
    for name, data in (("status", status()), ("storage", storage()), ("devices", DEVICES)):
        (site / "local" / f"{name}.json").write_text(json.dumps(data))

    server = ThreadingHTTPServer(("127.0.0.1", 8080), partial(SimpleHTTPRequestHandler, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--host-resolver-rules=MAP dashboard.home.example.com 127.0.0.1"])
        page = browser.new_page(viewport={"width": 1280, "height": 860}, device_scale_factor=2,
                                color_scheme=scheme, timezone_id=TZ)
        page.clock.set_fixed_time(T)
        for name, frag in SHOTS:
            page.goto(f"http://dashboard.home.example.com:8080/{frag}")
            page.wait_for_load_state("networkidle")
            page.evaluate("document.fonts.ready")
            path = OUT / f"dashboard-{name}.png"
            page.screenshot(path=str(path), full_page=True)
            print(path.relative_to(REPO))
        browser.close()
    server.shutdown()


if __name__ == "__main__":
    main()
