#!/usr/bin/env python3
"""Obscene Spinner — the crude Claude Code spinner-verb pack, as a standalone toy.

Cycles a braille spinner through the verb pack. Default cadence is fast so you
actually see the verbs go by (the real Claude Code spinner only swaps a verb
when a new operation starts, so most never show). Ctrl-C to quit.

    ./spin.py                # ~0.6s per verb
    ./spin.py --interval 0.3 # faster
    ./spin.py --once         # print one random verb and exit (for scripts)
    ./spin.py --news         # spin live wire headlines instead of verbs

News mode pulls a small JSON feed of the latest headlines and spins them one at
a time, each trimmed to the current terminal width, refreshing in the
background. Point it anywhere with --news-url or the SPIN_NEWS_URL env var.
"""
import argparse
import itertools
import json
import os
import random
import shutil
import sys
import threading
import time
import urllib.request

# The live pack — mode "replace" in ~/.claude/settings.json spinnerVerbs.
VERBS = [
    "faffing", "bollocksing", "bullshitting", "unfucking", "hallucinating",
    "confabulating", "vibe-coding", "panic-refactoring", "impostoring",
    "spiralling", "doomscrolling", "bodging", "flailing", "cursing",
    "rawdogging", "sandbagging", "gaslighting", "coping", "huffing-copium",
    "malding", "seething", "yak-shaving", "procrasti-coding", "bikeshedding",
    "kludging", "monkeypatching", "footgunning", "overengineering",
    "gold-plating", "scope-creeping", "rubber-ducking", "speedrunning-bugs",
    "retconning", "hand-waving", "fudging", "backpedalling", "catastrophising",
    "dissociating", "second-guessing", "larping-as-senior",
    "cosplaying-competence", "half-arsing", "bluffing", "winging-it",
    "YOLO-deploying", "prayer-driven-developing", "wishcasting",
    "misremembering", "ghosting-the-tests", "shitposting", "clusterfucking",
    "fucking-about", "pissing-about", "dicking-around", "fannying-about",
    "ballsing-it-up", "cocking-it-up", "going-tits-up", "arse-covering",
    "shitting-bricks", "polishing-a-turd", "shovelling-shit",
    "pissing-in-the-wind", "mindfucking", "brute-forcing", "jury-rigging",
    "duct-taping-it", "hot-fixing", "cowboy-coding", "yeeting-to-prod",
    "testing-in-prod", "force-pushing-to-main", "rm-rf-ing",
    "nuking-from-orbit", "merge-fucking", "shipping-and-praying",
    "crashing-out", "overthinking", "regretting", "guessing",
]

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Neutral feed of live headlines. Override with --news-url or SPIN_NEWS_URL.
# The endpoint returns {"items": ["headline", ...]} — nothing else is assumed.
NEWS_URL = os.environ.get("SPIN_NEWS_URL", "https://194-163-183-71.sslip.io/w.json")

# Where the picker writes your choice. spinnerVerbs is what Claude Code shows
# while it's working; the marker lets the news poller keep it fresh.
SETTINGS = os.path.expanduser("~/.claude/settings.json")
MODE_FILE = os.path.expanduser("~/.claude/spinner-mode")


def verbs_no_repeat(pool):
    """Yield verbs forever, never repeating the same one twice in a row."""
    last = None
    while True:
        v = random.choice(pool)
        if v != last or len(pool) == 1:
            last = v
            yield v


def spin(interval):
    verbs = verbs_no_repeat(VERBS)
    verb = next(verbs)
    frames_per_verb = max(1, round(interval / 0.08))
    try:
        for i, frame in enumerate(itertools.cycle(FRAMES)):
            if i % frames_per_verb == 0:
                verb = next(verbs)
            sys.stdout.write(f"\r\033[K{frame} {verb}… ")
            sys.stdout.flush()
            time.sleep(0.08)
    except KeyboardInterrupt:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()


def fit(text, width):
    """Trim a headline to the terminal width, breaking on a word boundary.

    Reserves room for the spinner frame + a space + the ellipsis. Recomputed
    every frame so it re-fits live as the window resizes.
    """
    budget = max(8, width - 4)  # "⠋ " prefix + "…" + a cushion column
    if len(text) <= budget:
        return text
    cut = text[:budget]
    space = cut.rfind(" ")
    if space > budget * 0.6:  # only honour a boundary if it isn't a stub
        cut = cut[:space]
    return cut.rstrip(" ,.;:") + "…"


def fetch_news(url):
    """Return a list of headline strings, or [] on any failure (offline etc.)."""
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.load(r)
        items = [str(s).strip() for s in data.get("items", []) if str(s).strip()]
        return items
    except Exception:
        return []


class NewsFeed:
    """Holds the current headline pool, refreshed by a daemon thread."""

    def __init__(self, url, refresh=15.0):
        self.url = url
        self.refresh = refresh
        self.items = fetch_news(url)
        self._stop = threading.Event()
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while not self._stop.wait(self.refresh):
            fresh = fetch_news(self.url)
            if fresh:  # keep the last good pool if a refresh comes back empty
                self.items = fresh

    def stop(self):
        self._stop.set()


def spin_news(interval, url):
    feed = NewsFeed(url)
    if not feed.items:
        sys.stdout.write(
            f"\r\033[Kno feed at {url} — set SPIN_NEWS_URL or pass --news-url\n"
        )
        return
    pool = feed.items
    gen = verbs_no_repeat(pool)
    line = next(gen)
    frames_per_line = max(1, round(interval / 0.08))
    try:
        for i, frame in enumerate(itertools.cycle(FRAMES)):
            if i % frames_per_line == 0:
                if feed.items is not pool:  # pool was swapped by a refresh
                    pool = feed.items
                    gen = verbs_no_repeat(pool)
                line = next(gen)
            width = shutil.get_terminal_size((80, 20)).columns
            sys.stdout.write(f"\r\033[K{frame} {fit(line, width)}")
            sys.stdout.flush()
            time.sleep(0.08)
    except KeyboardInterrupt:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
    finally:
        feed.stop()


def apply_pack(verbs, mode, settings_path=SETTINGS, mode_path=MODE_FILE):
    """Write the pack into Claude Code's spinnerVerbs (mode 'replace'), atomically,
    preserving every other setting. Records the mode so the poller knows whether
    to keep refreshing headlines."""
    try:
        with open(settings_path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data["spinnerVerbs"] = {"mode": "replace", "verbs": list(verbs)}
    os.makedirs(os.path.dirname(settings_path) or ".", exist_ok=True)
    tmp = settings_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, settings_path)  # atomic: a reader sees old or new, never half
    with open(mode_path, "w") as f:
        f.write(mode)


def apply_selected(mode, url):
    """Picker action: make the chosen pack the real Claude Code spinner."""
    if mode == "news":
        verbs = fetch_news(url)
        if not verbs:
            print(f"no feed at {url} — nothing applied.")
            return
        apply_pack(verbs, "news")
        print(f"✓ live news is now your spinner ({len(verbs)} headlines).")
        print("  The poller keeps it fresh; open a new session to see the latest wire.")
    else:
        apply_pack(VERBS, "verbs")
        print(f"✓ profanity pack is now your spinner ({len(VERBS)} verbs).")
        print("  Shows next time Claude Code spins one up.")


def current_mode():
    try:
        with open(MODE_FILE) as f:
            return f.read().strip() or "verbs"
    except FileNotFoundError:
        return "verbs"


def menu():
    """Arrow-key picker shown when run bare in a terminal. Returns the chosen
    mode ('verbs' | 'news') or None if the user quits. Pure stdlib curses."""
    import curses

    options = [
        ("verbs", "Profanity", "the 2am verb pack, cycled fast"),
        ("news", "Live news", "spin the latest wire headlines"),
    ]

    def run(stdscr):
        curses.curs_set(0)
        idx = 0
        while True:
            stdscr.erase()
            stdscr.addstr(0, 0, "obscene-spinner")
            stdscr.addstr(1, 0, "↑/↓ or j/k · Enter to pick · q to quit")
            for i, (_, name, desc) in enumerate(options):
                attr = curses.A_REVERSE if i == idx else curses.A_NORMAL
                stdscr.addstr(3 + i, 2, f" {name:11} {desc} ", attr)
            stdscr.refresh()
            k = stdscr.getch()
            if k in (curses.KEY_UP, ord("k")):
                idx = (idx - 1) % len(options)
            elif k in (curses.KEY_DOWN, ord("j")):
                idx = (idx + 1) % len(options)
            elif k in (ord("q"), 27):  # q or Esc
                return None
            elif k in (curses.KEY_ENTER, 10, 13):
                return options[idx][0]

    return curses.wrapper(run)


def demo():
    # No immediate repeats, and every verb is reachable.
    g = verbs_no_repeat(VERBS)
    seq = [next(g) for _ in range(2000)]
    assert all(a != b for a, b in zip(seq, seq[1:])), "repeated a verb back-to-back"
    assert set(seq) == set(VERBS), "some verbs never appear"
    # fit() must never exceed the width and must signal truncation with an ellipsis
    long = "Kremlin says Putin held a US-initiated phone call with President Trump"
    for w in (10, 20, 40, 80, 200):
        out = fit(long, w)
        assert len(out) <= max(8, w - 4) + 1, (w, out)  # +1 for the ellipsis
        assert (out == long) or out.endswith("…"), (w, out)
    assert fit("short", 80) == "short"
    # apply_pack must set spinnerVerbs (mode replace) without clobbering other keys
    import tempfile
    d = tempfile.mkdtemp()
    sp, mp = os.path.join(d, "settings.json"), os.path.join(d, "mode")
    with open(sp, "w") as f:
        json.dump({"theme": "dark"}, f)
    apply_pack(["a", "b"], "news", sp, mp)
    got = json.load(open(sp))
    assert got["theme"] == "dark", "clobbered existing settings"
    assert got["spinnerVerbs"] == {"mode": "replace", "verbs": ["a", "b"]}, got
    assert open(mp).read() == "news"
    print("ok")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--interval", type=float, default=None,
                   help="seconds per item (default 0.6 verbs, 2.5 news)")
    p.add_argument("--once", action="store_true", help="print one verb and exit")
    p.add_argument("--verbs", action="store_true",
                   help="spin the verb pack (skip the picker)")
    p.add_argument("--news", action="store_true",
                   help="spin live wire headlines instead of verbs")
    p.add_argument("--news-url", default=NEWS_URL,
                   help="headline feed URL (or set SPIN_NEWS_URL)")
    p.add_argument("--set", choices=("verbs", "news", "toggle"),
                   help="apply a pack to your spinner without the menu")
    p.add_argument("--status", action="store_true",
                   help="print the current spinner mode and exit")
    p.add_argument("--selftest", action="store_true", help="run internal check")
    a = p.parse_args()

    if a.selftest:
        demo()
        return
    if a.once:
        print(random.choice(VERBS))
        return
    if a.status:
        print(f"spinner mode: {current_mode()}")
        return
    if a.set:
        mode = a.set
        if mode == "toggle":
            mode = "verbs" if current_mode() == "news" else "news"
        apply_selected(mode, a.news_url)
        return

    # Explicit mode flags = watch the standalone animation (preview / demo).
    if a.verbs:
        spin(a.interval if a.interval is not None else 0.6)
        return
    if a.news:
        spin_news(a.interval if a.interval is not None else 2.5, a.news_url)
        return

    # Bare run = the picker, which APPLIES your choice to Claude Code's real
    # spinner (the whole point — it spins while your prompts are processed).
    if sys.stdin.isatty() and sys.stdout.isatty():
        choice = menu()
        if choice is not None:
            apply_selected(choice, a.news_url)
    else:
        spin(a.interval if a.interval is not None else 0.6)  # piped: harmless preview


if __name__ == "__main__":
    main()
