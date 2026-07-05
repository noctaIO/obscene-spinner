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
In the news ticker press n to read the current story's summary, q to quit.
"""
import argparse
import itertools
import json
import os
import random
import shutil
import sys
import textwrap
import threading
import time
import unicodedata
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
NEWS_URL = os.environ.get(
    "SPIN_NEWS_URL", "https://194-163-183-71.sslip.io/markets.json"
)

# Where the picker writes your choice. spinnerVerbs is what Claude Code shows
# while it's working; the marker lets the news poller keep it fresh.
SETTINGS = os.path.expanduser("~/.claude/settings.json")
MODE_FILE = os.path.expanduser("~/.claude/spinner-mode")


def shuffle_bag(pool):
    """Yield items forever in reshuffled order, dealing the WHOLE pool before any
    repeat (like a deck of cards, not a die roll). random.choice would resample
    with replacement, so a 50-item feed still felt like the same handful over and
    over; this guarantees you see every item once per cycle. Also avoids a repeat
    at the reshuffle seam."""
    last = None
    while True:
        bag = list(pool)
        random.shuffle(bag)
        if last is not None and len(bag) > 1 and bag[0] == last:
            bag.append(bag.pop(0))  # don't repeat across the shuffle boundary
        for item in bag:
            last = item
            yield item


def spin(interval):
    verbs = shuffle_bag(VERBS)
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


def char_width(ch):
    """Terminal COLUMNS one character occupies (not codepoints).

    A terminal lays out cells, not codepoints, so len() is the wrong ruler:
    - Combining marks (é as e + U+0301) attach to the previous cell -> 0 cols.
    - Control/format chars (category Cc/Cf, e.g. U+200B zero-width space) -> 0.
    - East-Asian Wide/Fullwidth (CJK, most emoji, fullwidth digits) -> 2 cols.
    - Everything else -> 1 col.
    """
    if unicodedata.combining(ch):
        return 0
    if unicodedata.category(ch) in ("Cc", "Cf", "Mn", "Me"):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def disp_width(s):
    """Rendered column width of a string (sum of per-char cell widths)."""
    return sum(char_width(c) for c in s)


def fit(text, width):
    """Trim a headline to the terminal width by DISPLAY columns, not codepoints.

    The caller prints "{frame} {fit(text, width)}", so a fixed 2-column prefix
    (spinner glyph + space) sits to the left. We reserve that prefix, one column
    for the trailing ellipsis, and one final safety column so a full line never
    lands in the last cell (which triggers terminal auto-wrap). The guarantee is:

        2 (prefix) + disp_width(fit(text, width)) <= width - 1

    Recomputed every frame so it re-fits live as the window resizes. CJK, emoji
    and NFD-decomposed accents are all measured in real columns, so wide feeds no
    longer overflow and accented Latin no longer under-fills.
    """
    PREFIX = 2       # "{frame} " -> spinner glyph (1 col) + space (1 col)
    ELLIPSIS = 1     # "…" is a single column
    SAFE = 1         # never write the final column -> avoid auto-wrap
    budget = max(1, width - PREFIX - SAFE)  # columns available to the text region

    if disp_width(text) <= budget:
        return text

    # Reserve the ellipsis, then walk accumulating real column widths.
    keep_budget = budget - ELLIPSIS
    kept_cols = 0
    cut_end = 0
    for i, ch in enumerate(text):
        w = char_width(ch)
        if w == 0:
            # Combining mark: attaches to the char we just kept, costs nothing.
            # Keep it so we don't strip an accent off its base letter.
            cut_end = i + 1
            continue
        if kept_cols + w > keep_budget:
            break
        kept_cols += w
        cut_end = i + 1
    cut = text[:cut_end]

    # Prefer a word boundary so the truncated headline is still intelligible.
    # rfind(' ') returns -1 for CJK/unbreakable text — fall back to the char cut.
    # ws > 0 (not >= 0): a space at index 0 would produce an empty cut, which is
    # worse than the character-level result.
    ws = cut.rfind(' ')
    if ws > 0:
        cut = cut[:ws]
    result = cut.rstrip(" ,.;:—") + "…"
    # Final guarantee: prefix + rendered width never reaches the last column.
    assert PREFIX + disp_width(result) <= width - SAFE, (width, result)
    return result


def normalize_items(raw):
    """Normalise feed items to [{title, summary}]. Accepts plain strings (the
    world feed) or {title/summary/description} objects (the markets feed carries
    a summary so the ticker can show it when you press n). Drops empty titles."""
    out = []
    for it in raw or []:
        if isinstance(it, dict):
            title = str(it.get("title", "")).strip()
            summary = str(it.get("summary") or it.get("description") or "").strip()
        else:
            title, summary = str(it).strip(), ""
        if title:
            out.append({"title": title, "summary": summary or None})
    return out


def fetch_news(url):
    """Return a list of {title, summary} dicts, or [] on any failure (offline)."""
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.load(r)
        return normalize_items(data.get("items", []))
    except Exception:
        return []


def _read_key(timeout=0):
    """One keystroke if one is waiting (non-blocking when timeout=0, blocking when
    timeout=None), else None. Only valid while the terminal is in cbreak mode."""
    import select
    if timeout is None or select.select([sys.stdin], [], [], timeout)[0]:
        return sys.stdin.read(1)
    return None


def show_summary(item):
    """Pause the ticker and print the current story's summary; wait for a key."""
    title = item.get("title", "")
    summary = item.get("summary")
    width = min(shutil.get_terminal_size((80, 20)).columns, 100)
    parts = ["\r\033[K\n\033[1m", title, "\033[0m\n\n"]  # bold title, fresh line
    parts.append("\n".join(textwrap.wrap(summary, width)) if summary
                 else "(no summary for this headline)")
    parts.append("\n\n\033[2m— press n to close —\033[0m\n")
    sys.stdout.write("".join(parts))
    sys.stdout.flush()
    while True:  # wait specifically for 'n', not just any key
        k = _read_key(timeout=None)
        if k and k.lower() == "n":
            break


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
    snapshot = tuple(feed.items)
    gen = shuffle_bag(list(snapshot))
    line = next(gen)
    frames_per_line = max(1, round(interval / 0.08))

    # Interactive keys only work on a real terminal. cbreak mode lets us read one
    # keystroke at a time without waiting for Enter, and leaves Ctrl-C working.
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    old_term = fd = None
    if interactive:
        import termios, tty
        fd = sys.stdin.fileno()
        old_term = termios.tcgetattr(fd)
        tty.setcbreak(fd)

    try:
        for i, frame in enumerate(itertools.cycle(FRAMES)):
            if i % frames_per_line == 0:
                current = tuple(feed.items)
                # Rebuild the deck only when the headlines ACTUALLY changed
                # (compare by value, not identity, or every refresh resets it).
                if current and current != snapshot:
                    snapshot = current
                    gen = shuffle_bag(list(current))
                line = next(gen)
            width = shutil.get_terminal_size((80, 20)).columns
            if interactive:
                # Two-line display: ticker on the current line, status bar below.
                # After writing both lines, \033[1A moves the cursor back up to the
                # ticker row so the next frame can overwrite cleanly from the same
                # position.  \r resets the column regardless of where we ended up.
                hint = "\033[2m[n] summary  [q] quit\033[0m"  # two spaces for visual breathing room
                sys.stdout.write(
                    f"\r\033[K{frame} {fit(line['title'], width)}"
                    f"\n\r\033[K{hint}\033[1A"
                )
            else:
                sys.stdout.write(f"\r\033[K{frame} {fit(line['title'], width)}")
            sys.stdout.flush()
            time.sleep(0.08)
            if interactive:
                key = _read_key()
                if key in ("n", "N"):
                    show_summary(line)
                elif key in ("q", "Q", "\x1b"):  # q or Esc
                    break
    except KeyboardInterrupt:
        pass
    finally:
        if old_term is not None:
            import termios
            termios.tcsetattr(fd, termios.TCSADRAIN, old_term)
        feed.stop()
        # Clear both lines (ticker + status bar) so the prompt is left clean:
        # go to col 0 on ticker line, erase it, step down, erase status bar, step back up.
        sys.stdout.write("\r\033[K\n\r\033[K\033[1A")
        sys.stdout.flush()


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
        verbs = [fit(v["title"], 64) for v in verbs]  # titles only, one line each
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
    # Shuffle bag: no back-to-back repeats, every verb reachable, and — the whole
    # point of the fix — a full cycle of length N shows every verb exactly once
    # before any repeat.
    g = shuffle_bag(VERBS)
    seq = [next(g) for _ in range(2000)]
    assert all(a != b for a, b in zip(seq, seq[1:])), "repeated a verb back-to-back"
    assert set(seq) == set(VERBS), "some verbs never appear"
    n = len(VERBS)
    assert len(set(seq[:n])) == n, "first full cycle must show every verb once"
    assert len(set(seq[n:2 * n])) == n, "second cycle must also be a full sweep"
    # fit() must never overflow: the rendered "{frame} {fit(text,w)}" line must
    # fit in width-1 columns (last cell left blank to dodge auto-wrap), measured
    # in DISPLAY columns. Exercise the wide-char (CJK/emoji), NFD-combining, and
    # unbreakable-word paths — the codepoint model got all of these wrong.
    import unicodedata as _ud
    headlines = [
        # long ASCII with plenty of word boundaries
        "Kremlin says Putin held a US-initiated phone call with President Trump today",
        # CJK: every glyph is 2 columns, no spaces to break on
        "中国A股上证指数创下新高投资者观望美联储利率决议",
        # emoji (width-2) mixed into ASCII
        "Markets rip higher 🚀 as the Fed signals a surprise pause on rate hikes",
        # NFD-decomposed accents: café/señor as base + combining mark
        _ud.normalize("NFD", "Café señor naïve résumé façade — accented world news roundup"),
        # single unbreakable 120-char word: no space anywhere to snap to
        "x" * 120,
    ]
    for text in headlines:
        for w in (6, 8, 10, 20, 40, 80, 120, 200):
            out = fit(text, w)
            line = f"{FRAMES[0]} {out}"
            assert disp_width(line) <= w - 1, (w, repr(text), repr(out),
                                               disp_width(line))
            if out != text:
                assert out.endswith("…"), (w, repr(text), repr(out))
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
    # normalize_items accepts plain strings AND {title, summary/description} dicts
    assert normalize_items(["a", "b"]) == [
        {"title": "a", "summary": None}, {"title": "b", "summary": None}]
    assert normalize_items([{"title": "T", "summary": "S"}]) == [
        {"title": "T", "summary": "S"}]
    assert normalize_items([{"title": "T", "description": "D"}])[0]["summary"] == "D"
    assert normalize_items([{"title": ""}, "  "]) == []  # drops empty titles
    print("ok")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--interval", type=float, default=None,
                   help="seconds per item (default 0.6 verbs, 5 news)")
    p.add_argument("--once", action="store_true", help="print one verb and exit")
    p.add_argument("--verbs", action="store_true",
                   help="spin the verb pack (skip the picker)")
    p.add_argument("--news", action="store_true",
                   help="spin live headlines (press n to read a summary)")
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
        spin_news(a.interval if a.interval is not None else 5.0, a.news_url)
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
