#!/usr/bin/env python3
"""Obscene Spinner — a whole gallery of spinner packs for Claude Code.

Claude Code lets you replace the verbs it shows while it's working. This picks
them. Twenty-odd word packs, twenty-odd animations and a dozen colour themes,
mixed and matched, browsed in a live-animating terminal gallery, and written
straight into ~/.claude/settings.json.

    ./spin.py                      # the gallery — browse everything, Enter applies
    ./spin.py --list               # every pack, animation and theme as text
    ./spin.py --pack eldritch      # preview one pack in the terminal
    ./spin.py --set british        # apply a pack, no menu
    ./spin.py --wall               # every animation running at once
    ./spin.py --restore            # put your old spinnerVerbs back

Still one Python 3 file. Still no dependencies, no build, no config.
"""
import argparse
import colorsys
import glob
import itertools
import json
import os
import random
import re
import shutil
import sys
import textwrap
import threading
import time
import unicodedata
import urllib.request

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

# Where the picker writes your choice. spinnerVerbs is what Claude Code shows
# while it's working; the marker lets the news poller keep it fresh.
CLAUDE_DIR = os.path.expanduser("~/.claude")
SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
MODE_FILE = os.path.join(CLAUDE_DIR, "spinner-mode")      # legacy: "verbs"/"news"
STATE_FILE = os.path.join(CLAUDE_DIR, "spinner-state.json")
BACKUP_FILE = os.path.join(CLAUDE_DIR, "spinner-backup.json")
USER_PACK_DIR = os.path.join(CLAUDE_DIR, "spinner-packs")

# Claude Code's spinner COLOUR is reachable: a custom theme file whose `claude`
# token is documented as "primary brand accent, used for the spinner". The
# filename is the slug, and Claude Code watches this directory and hot-reloads,
# so rewriting the file recolours a running session. We keep exactly one file
# and rewrite it on every apply.
THEME_DIR = os.path.join(CLAUDE_DIR, "themes")
THEME_SLUG = "obscene-spinner"
THEME_FILE = os.path.join(THEME_DIR, THEME_SLUG + ".json")
THEME_BASES = ("dark", "light", "dark-daltonized", "light-daltonized",
               "dark-ansi", "light-ansi")
DEFAULT_THEME_BASE = "dark"

# Claude Code's spinner GLYPH is not reachable — there is no setting for it, so
# it always draws this. The gallery's "as Claude Code will draw it" pane uses
# this rather than whichever animation you're previewing, or it would be lying.
CLAUDE_SPINNER = "braille"

# Neutral feed of live headlines. Override with --news-url or SPIN_NEWS_URL.
# The endpoint returns {"items": ["headline", ...]} — nothing else is assumed.
NEWS_URL = os.environ.get(
    "SPIN_NEWS_URL", "https://194-163-183-71.sslip.io/markets.json"
)

# Claude Code renders the spinner verb line at ~56 columns and chops anything
# past it. fit() would truncate mid-word behind an ellipsis, so the applied
# headline reads as a dangling fragment. condense() instead SHORTENS the whole
# thought to fit, so the line still reads as a headline.
SPINNER_MAX = 56

TICK = 0.08          # base frame clock, seconds
VERB_DWELL = 1.1     # seconds a sample verb stays on screen in the gallery

RATINGS = ("sfw", "mild", "nsfw")


# ---------------------------------------------------------------------------
# Display width — a terminal lays out cells, not codepoints
# ---------------------------------------------------------------------------

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


def fit(text, width, prefix=2):
    """Trim a line to the terminal width by DISPLAY columns, not codepoints.

    The caller prints "{frame} {fit(text, width, prefix)}", so a fixed prefix
    (spinner glyph + space) sits to the left. Braille glyphs are one column and
    emoji animations are two, so the prefix is a parameter rather than a
    constant. We reserve that prefix, one column for the trailing ellipsis, and
    one final safety column so a full line never lands in the last cell (which
    triggers terminal auto-wrap). The guarantee is:

        prefix + disp_width(fit(text, width, prefix)) <= width - 1

    Recomputed every frame so it re-fits live as the window resizes. CJK, emoji
    and NFD-decomposed accents are all measured in real columns, so wide feeds no
    longer overflow and accented Latin no longer under-fills.
    """
    ELLIPSIS = 1     # "…" is a single column
    SAFE = 1         # never write the final column -> avoid auto-wrap
    budget = width - prefix - SAFE   # columns available to the text
    if budget < 1:
        # A wide glyph in a very narrow window can leave no room at all. Return
        # nothing rather than an ellipsis that would itself overflow — the
        # caller's guarantee is a hard bound, not a best effort.
        return ""

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

    # Fill to the maximum: cut mid-word so every available column is used (the
    # trailing "…" already signals truncation). Only trim a dangling space or
    # punctuation so the ellipsis doesn't sit after a space or comma. We used to
    # snap back to the last whole word, but that wasted up to ~15% of the line.
    result = cut.rstrip(" ,.;:") + "…"
    # Final guarantee: prefix + rendered width never reaches the last column.
    assert prefix + disp_width(result) <= width - SAFE, (width, prefix, result)
    return result


def plural(n, word, suffix="s"):
    return "%d %s%s" % (n, word, "" if n == 1 else suffix)


def pad(text, width):
    """Left-justify to `width` DISPLAY columns (str.ljust counts codepoints)."""
    gap = width - disp_width(text)
    return text + " " * gap if gap > 0 else text


def rpad(text, width):
    """Right-justify to `width` DISPLAY columns."""
    gap = width - disp_width(text)
    return " " * gap + text if gap > 0 else text


# ---------------------------------------------------------------------------
# Deck-style shuffling
# ---------------------------------------------------------------------------

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


def stable_order(key, items):
    """A shuffled-but-deterministic ordering of `items`, seeded by `key`.

    The gallery animates dozens of rows at once and is redrawn from scratch on
    every tick, so it can't hold a shuffle_bag generator per row — it needs to
    ask "which verb at time t?" and get a stable answer. Seeding by pack id
    means each pack looks shuffled, every run looks the same, and the whole
    thing stays a pure function of the clock (which is what makes it testable).
    """
    order = list(items)
    random.Random(key).shuffle(order)
    return order


# ---------------------------------------------------------------------------
# Animations
# ---------------------------------------------------------------------------
# Every frame within a set MUST render at the same display width, or the verb
# beside it jumps left and right on each tick. tests/test_spin.py enforces it.

def _f(s):
    """Frames from a string of single-codepoint glyphs."""
    return list(s)


SPINNERS = {
    "braille":    {"name": "Braille",     "interval": 0.08, "frames": _f("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")},
    "braille2":   {"name": "Braille bold", "interval": 0.08, "frames": _f("⣾⣽⣻⢿⡿⣟⣯⣷")},
    "braille3":   {"name": "Braille dot",  "interval": 0.10, "frames": _f("⠁⠂⠄⡀⢀⠠⠐⠈")},
    "dots":       {"name": "Dots",        "interval": 0.08, "frames": _f("⢹⢺⢼⣸⣇⡧⡗⡏")},
    "bounce":     {"name": "Bounce",      "interval": 0.12, "frames": _f("⠁⠂⠄⠂")},
    "line":       {"name": "Line",        "interval": 0.13, "frames": _f("|/-\\")},
    "layer":      {"name": "Layer",       "interval": 0.15, "frames": _f("-=≡=")},
    "pipe":       {"name": "Pipe",        "interval": 0.10, "frames": _f("┤┘┴└├┌┬┐")},
    "arc":        {"name": "Arc",         "interval": 0.10, "frames": _f("◜◠◝◞◡◟")},
    "circle":     {"name": "Circle",      "interval": 0.12, "frames": _f("◐◓◑◒")},
    "quadrant":   {"name": "Quadrant",    "interval": 0.12, "frames": _f("◴◷◶◵")},
    "square":     {"name": "Square",      "interval": 0.12, "frames": _f("◰◱◲◳")},
    "half":       {"name": "Half",        "interval": 0.14, "frames": _f("◧◨◩◪")},
    "triangle":   {"name": "Triangle",    "interval": 0.12, "frames": _f("◢◣◤◥")},
    "corner":     {"name": "Corner",      "interval": 0.10, "frames": _f("▖▘▝▗")},
    "toggle":     {"name": "Toggle",      "interval": 0.25, "frames": _f("⊶⊷")},
    "switch":     {"name": "Switch",      "interval": 0.22, "frames": _f("▮▯")},
    "blocks":     {"name": "Blocks",      "interval": 0.07, "frames": _f("▁▂▃▄▅▆▇█▇▆▅▄▃▂")},
    "grow":       {"name": "Grow",        "interval": 0.07, "frames": _f("▏▎▍▌▋▊▉█▉▊▋▌▍▎")},
    "noise":      {"name": "Noise",       "interval": 0.10, "frames": _f("░▒▓█▓▒")},
    "pulse":      {"name": "Pulse",       "interval": 0.13, "frames": _f("·•●•")},
    "star":       {"name": "Star",        "interval": 0.12, "frames": _f("✶✸✹✺✹✷")},
    "arrows":     {"name": "Arrows",      "interval": 0.10, "frames": _f("←↖↑↗→↘↓↙")},
    "arrows2":    {"name": "Arrows bold", "interval": 0.10, "frames": _f("⇐⇖⇑⇗⇒⇘⇓⇙")},
    "orbit":      {"name": "Orbit",       "interval": 0.10, "frames": _f("◜◝◞◟")},
    "flip":       {"name": "Flip",        "interval": 0.11, "frames": _f("_-`'´-_ ")},
    "dqpb":       {"name": "dqpb",        "interval": 0.10, "frames": _f("dqpb")},
    "binary":     {"name": "Binary",      "interval": 0.09,
                   "frames": ["0010", "1010", "1101", "0110", "1001", "0100",
                              "1110", "0011"]},
    "bar":        {"name": "Bar",         "interval": 0.13,
                   "frames": ["▰▱▱▱▱", "▰▰▱▱▱", "▰▰▰▱▱", "▰▰▰▰▱", "▰▰▰▰▰",
                              "▱▰▰▰▰", "▱▱▰▰▰", "▱▱▱▰▰", "▱▱▱▱▰", "▱▱▱▱▱"]},
    "point":      {"name": "Point",       "interval": 0.13,
                   "frames": ["∙∙∙", "●∙∙", "∙●∙", "∙∙●"]},
    "balloon":    {"name": "Balloon",     "interval": 0.14, "frames": _f(" .oO@* ")},
    "moon":       {"name": "Moon",        "interval": 0.13, "frames": _f("🌑🌒🌓🌔🌕🌖🌗🌘")},
    "earth":      {"name": "Earth",       "interval": 0.20, "frames": _f("🌍🌎🌏")},
    "clock":      {"name": "Clock face",  "interval": 0.10,
                   "frames": _f("🕐🕑🕒🕓🕔🕕🕖🕗🕘🕙🕚🕛")},
    "hearts":     {"name": "Hearts",      "interval": 0.16, "frames": _f("💛💙💜💚🧡")},
    "static":     {"name": "Static",      "interval": 1.00, "frames": ["●"]},
}

DEFAULT_SPINNER = "braille"

# Legacy alias — the original module-level frame string.
FRAMES = "".join(SPINNERS[DEFAULT_SPINNER]["frames"])


def get_spinner(sid):
    """Look up an animation, falling back to the default rather than raising —
    a stale ~/.claude state file must never stop the tool from starting."""
    return SPINNERS.get(sid) or SPINNERS[DEFAULT_SPINNER]


def frame_width(sid):
    """Display columns of this animation's frames (all frames are equal width)."""
    return disp_width(get_spinner(sid)["frames"][0])


def frame_at(sid, now, phase=0.0):
    """Which frame of `sid` is showing at time `now` — a pure function of the
    clock, not a counter. The gallery animates ~30 rows at different intervals
    and redraws them all from scratch each tick; deriving the index from elapsed
    time keeps every row independent and immune to a slow redraw dropping a
    beat. `phase` offsets a row so they don't all march in lockstep."""
    sp = get_spinner(sid)
    frames = sp["frames"]
    return frames[int((now + phase) / sp["interval"]) % len(frames)]


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------
# Themes are defined once in RGB. Everything else — the 256-colour cube, the
# 16-colour fallback, curses colour pairs — is derived, so adding a theme is one
# line and can't get out of sync across renderers.

THEMES = {
    "acid":    {"name": "Acid",    "frame": (0xB5, 0xFF, 0x00), "accent": (0x39, 0xFF, 0x14),
                "text": (0xE6, 0xED, 0xF3), "style": "solid"},
    "ember":   {"name": "Ember",   "frame": (0xFF, 0x6B, 0x1A), "accent": (0xFF, 0xD2, 0x3F),
                "text": (0xF5, 0xE0, 0xC8), "style": "gradient"},
    "void":    {"name": "Void",    "frame": (0x9D, 0x4E, 0xDD), "accent": (0x3C, 0x09, 0x66),
                "text": (0xC8, 0xB6, 0xE2), "style": "gradient"},
    "ice":     {"name": "Ice",     "frame": (0x5B, 0xC0, 0xEB), "accent": (0xD7, 0xF9, 0xFF),
                "text": (0xDA, 0xEE, 0xF7), "style": "gradient"},
    "matrix":  {"name": "Matrix",  "frame": (0x00, 0xFF, 0x41), "accent": (0x00, 0x8F, 0x11),
                "text": (0x00, 0xD1, 0x35), "style": "gradient"},
    "blood":   {"name": "Blood",   "frame": (0xC4, 0x14, 0x1C), "accent": (0x6A, 0x04, 0x0F),
                "text": (0xE8, 0xAE, 0xB0), "style": "gradient"},
    "gold":    {"name": "Gold",    "frame": (0xFF, 0xC1, 0x07), "accent": (0x8B, 0x63, 0x00),
                "text": (0xF3, 0xE5, 0xB8), "style": "gradient"},
    "vapor":   {"name": "Vapor",   "frame": (0xFF, 0x71, 0xCE), "accent": (0x01, 0xCD, 0xFE),
                "text": (0xE8, 0xD5, 0xF5), "style": "gradient"},
    "deep":    {"name": "Deep",    "frame": (0x00, 0x91, 0xAD), "accent": (0x00, 0x3F, 0x5C),
                "text": (0xBF, 0xDD, 0xE4), "style": "gradient"},
    "bone":    {"name": "Bone",    "frame": (0xD8, 0xD2, 0xC4), "accent": (0x8A, 0x84, 0x77),
                "text": (0xEA, 0xE6, 0xDC), "style": "solid"},
    "mono":    {"name": "Mono",    "frame": (0xE6, 0xED, 0xF3), "accent": (0x7D, 0x85, 0x90),
                "text": (0xE6, 0xED, 0xF3), "style": "solid"},
    "rainbow": {"name": "Rainbow", "frame": (0xFF, 0x00, 0x00), "accent": (0x00, 0x00, 0xFF),
                "text": (0xE6, 0xED, 0xF3), "style": "rainbow"},
}

DEFAULT_THEME = "acid"
RESET = "\033[0m"


def get_theme(tid):
    """Look up a theme, falling back to the default rather than raising."""
    return THEMES.get(tid) or THEMES[DEFAULT_THEME]


def color_mode(stream=None, force=None, env=None):
    """How much colour this terminal can take: truecolor | 256 | 16 | none.

    NO_COLOR (https://no-color.org) and a non-TTY both mean "none" — piping the
    spinner into a file or a status bar must produce clean text, not escape
    soup. `force` is --color/--no-color from the command line.
    """
    if force in ("truecolor", "256", "16", "none"):
        return force
    env = os.environ if env is None else env
    stream = sys.stdout if stream is None else stream
    if env.get("NO_COLOR") is not None:
        return "none"
    term = env.get("TERM", "")
    if term in ("dumb", ""):
        return "none"
    try:
        if not stream.isatty():
            return "none"
    except Exception:
        return "none"
    if env.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return "truecolor"
    if "256" in term or "direct" in term:
        return "256"
    return "16"


def rgb_to_256(rgb):
    """Nearest xterm-256 index. 16-231 is a 6x6x6 cube, 232-255 is a grey ramp;
    near-grey colours look markedly better on the ramp than in the cube."""
    r, g, b = rgb
    if abs(r - g) < 12 and abs(g - b) < 12 and abs(r - b) < 12:
        grey = round((r + g + b) / 3)
        if grey < 8:
            return 16
        if grey > 248:
            return 231
        return 232 + round((grey - 8) / 247 * 23)
    scale = lambda v: 0 if v < 48 else (1 if v < 115 else min(5, (v - 35) // 40))
    return 16 + 36 * scale(r) + 6 * scale(g) + scale(b)


def rgb_to_16(rgb):
    """Nearest basic ANSI colour 0-15 (bit-per-channel plus a brightness bit)."""
    r, g, b = rgb
    bits = (1 if r > 100 else 0) | (2 if g > 100 else 0) | (4 if b > 100 else 0)
    bright = 8 if max(rgb) > 170 else 0
    if bits == 0 and bright:
        bits = 7  # a bright "black" is grey, not invisible
    return bits | bright


def sgr(rgb, mode, bold=False):
    """SGR escape for a foreground colour in the given mode ("" when none)."""
    if mode == "none":
        return ""
    b = "1;" if bold else ""
    if mode == "truecolor":
        return "\033[%s38;2;%d;%d;%dm" % ((b,) + tuple(rgb))
    if mode == "256":
        return "\033[%s38;5;%dm" % (b, rgb_to_256(rgb))
    idx = rgb_to_16(rgb)
    base = 90 + (idx - 8) if idx >= 8 else 30 + idx
    return "\033[%s%dm" % (b, base)


def _lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def theme_rgb(theme, role, phase=0.0):
    """Colour for a role ("frame" or "text") at animation phase 0.0-1.0.

    Solid themes ignore the phase. Gradient themes ping-pong the frame colour
    between `frame` and `accent` so the glyph breathes. Rainbow sweeps hue.
    """
    th = get_theme(theme) if isinstance(theme, str) else theme
    if role == "text":
        return th["text"]
    style = th.get("style", "solid")
    if style == "rainbow":
        return tuple(round(c * 255) for c in colorsys.hsv_to_rgb(phase % 1.0, 0.85, 1.0))
    if style == "gradient":
        # Ping-pong: 0 -> 1 -> 0 over the cycle, so there's no jump at the seam.
        t = phase % 1.0
        return _lerp(th["frame"], th["accent"], t * 2 if t < 0.5 else (1 - t) * 2)
    return th["frame"]


def hex_of(rgb):
    """#rrggbb — the colour format Claude Code's theme files take."""
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(c))) for c in rgb)


def luminance(rgb):
    """Relative luminance, 0.0-1.0. Only used to compare two colours."""
    r, g, b = (c / 255.0 for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def lighten(rgb, amount=0.4):
    """Blend toward white."""
    return tuple(round(c + (255 - c) * amount) for c in rgb)


def shimmer_for(theme):
    """The lighter partner colour for Claude Code's spinner gradient.

    The docs describe claudeShimmer as "the lighter color used in the spinner's
    animated gradient", so it has to actually BE lighter. Half our gradient
    themes run bright -> dark (void, blood, deep, matrix), and naively handing
    over `accent` would give Claude Code a shimmer darker than the base colour
    and invert the animation. So take whichever of accent and a lightened frame
    is genuinely brighter.
    """
    th = get_theme(theme) if isinstance(theme, str) else theme
    frame = tuple(th["frame"])
    candidates = [tuple(th["accent"]), lighten(frame)]
    best = max(candidates, key=luminance)
    return best if luminance(best) > luminance(frame) else lighten(frame, 0.55)


def claude_theme_payload(theme, base=DEFAULT_THEME_BASE):
    """The exact JSON to write to ~/.claude/themes/<slug>.json.

    Only `claude` and `claudeShimmer` are set. `overrides` is documented as
    additive — "tokens not listed here fall through to the base preset" — so
    every other colour in the user's UI is left exactly as it was.
    """
    th = get_theme(theme) if isinstance(theme, str) else theme
    if base not in THEME_BASES:
        base = DEFAULT_THEME_BASE
    return {
        "name": "obscene-spinner (%s)" % th["name"],
        "base": base,
        "overrides": {
            "claude": hex_of(th["frame"]),
            "claudeShimmer": hex_of(shimmer_for(th)),
        },
    }


def write_claude_theme(theme, base=DEFAULT_THEME_BASE, path=THEME_FILE):
    """Write the theme file. Returns (payload, existed_before).

    Atomic, because Claude Code watches this directory — a half-written file
    would be read as a broken theme.
    """
    existed = os.path.exists(path)
    payload = claude_theme_payload(theme, base)
    _atomic_write_json(path, payload)
    return payload, existed


def remove_claude_theme(path=THEME_FILE):
    """Delete our theme file. Returns True if there was one."""
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def render_line(frame, text, theme, mode, phase=0.0, suffix="…"):
    """One rendered spinner line: coloured glyph, space, coloured text."""
    if mode == "none":
        return "%s %s%s" % (frame, text, suffix)
    fc = sgr(theme_rgb(theme, "frame", phase), mode, bold=True)
    tc = sgr(theme_rgb(theme, "text"), mode)
    return "%s%s%s %s%s%s%s" % (fc, frame, RESET, tc, text, suffix, RESET)


# ---------------------------------------------------------------------------
# Word packs
# ---------------------------------------------------------------------------
# id / name / desc / category / rating (sfw|mild|nsfw) / spinner / theme / verbs.
# Categories are just grouping labels for the gallery's tab filter.

PACKS = [
    {
        "id": "profanity", "name": "Profanity", "category": "rude", "rating": "nsfw",
        "desc": "the original 2am verb pack", "spinner": "braille", "theme": "acid",
        "verbs": [
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
        ],
    },
    {
        "id": "british", "name": "British disasters", "category": "rude", "rating": "nsfw",
        "desc": "everything has gone pear-shaped, mildly", "spinner": "line", "theme": "bone",
        "verbs": [
            "faffing", "bollocksing", "fannying-about", "arsing-about",
            "ballsing-it-up", "cocking-it-up", "going-tits-up", "chuffing-about",
            "knackering-it", "bodging-it", "gubbing-it", "naffing-it-up",
            "pissing-about", "dicking-about", "twatting-about", "having-a-mare",
            "throwing-a-wobbly", "doing-my-nut", "getting-shirty", "kicking-off",
            "going-pear-shaped", "going-up-the-spout", "going-round-the-houses",
            "making-a-pig's-ear-of-it", "taking-the-piss", "taking-the-mick",
            "wanging-on", "banging-on", "whingeing", "gurning", "sulking",
            "chuntering", "mithering", "wittering", "waffling", "dithering",
            "mucking-about", "messing-about", "larking-about", "skiving",
            "bunking-off", "malingering", "grumbling", "tutting", "sighing-heavily",
            "joining-the-queue", "apologising-to-furniture", "blaming-the-weather",
            "putting-the-kettle-on", "having-a-sit-down",
        ],
    },
    {
        "id": "scottish", "name": "Scottish", "category": "rude", "rating": "nsfw",
        "desc": "pure bealing, so it is", "spinner": "noise", "theme": "deep",
        "verbs": [
            "bealing", "greetin", "radgeing", "bawbagging", "gieing-it-laldy",
            "blootering-it", "steaming-in", "chibbing-the-build", "haudin-ma-wheesht",
            "gettin-tae-fuck", "tellin-it-tae-bile-its-heid", "pure-raging",
            "getting-scunnered", "boggin-it-up", "mingin-it-up", "wheeching",
            "birling", "guddling", "startin-a-stramash", "haverin", "blethering",
            "footering", "swithering", "dreeping", "girning", "nashing",
            "malkying-it", "doing-ma-dinger", "taking-a-riddy", "keeching-it",
            "patter-merchanting", "sook-arsing", "numptying-about", "wallopering",
            "roasting-it", "tanning-it", "gieing-it-big-licks", "hauding-the-heid",
            "coupin-it", "stoating-aboot",
        ],
    },
    {
        "id": "cope", "name": "Cope", "category": "feelings", "rating": "mild",
        "desc": "the five stages, compressed into one build", "spinner": "pulse",
        "theme": "vapor",
        "verbs": [
            "malding", "seething", "coping", "huffing-copium", "dissociating",
            "spiralling", "crashing-out", "doomscrolling", "doomposting",
            "catastrophising", "ruminating", "overthinking", "second-guessing",
            "regretting", "wallowing", "moping", "despairing", "rationalising",
            "deflecting", "projecting", "repressing", "compartmentalising",
            "trauma-dumping", "oversharing", "self-soothing", "stress-eating",
            "revenge-procrastinating", "bargaining", "denying",
            "negotiating-with-reality", "manifesting", "journalling-about-it",
            "touching-grass", "going-for-a-walk", "screaming-internally",
            "laughing-nervously", "staring-blankly", "rocking-gently",
            "needing-a-minute", "having-a-moment", "googling-therapists",
            "drafting-a-resignation",
        ],
    },
    {
        "id": "prod", "name": "Not in prod", "category": "engineering", "rating": "mild",
        "desc": "forty things you should never do to a live system",
        "spinner": "blocks", "theme": "blood",
        "verbs": [
            "yeeting-to-prod", "testing-in-prod", "debugging-in-prod",
            "force-pushing-to-main", "rm-rf-ing", "nuking-from-orbit",
            "dropping-the-table", "truncating-prod", "disabling-the-tests",
            "skipping-CI", "merging-unreviewed", "deploying-on-Friday",
            "hotfixing-blind", "sudo-ing-blindly", "chmod-777-ing",
            "hardcoding-the-token", "committing-the-env-file", "pushing-the-secret",
            "curl-piping-to-bash", "running-as-root", "ignoring-the-linter",
            "suppressing-the-warning", "catching-and-passing",
            "swallowing-the-exception", "sleeping-until-it-works",
            "retrying-forever", "restarting-the-box", "bouncing-prod",
            "rolling-back-the-rollback", "reverting-the-revert",
            "cherry-picking-chaos", "rebasing-shared-history",
            "amending-pushed-commits", "deleting-the-migration",
            "editing-the-database-directly", "patching-live", "monkeypatching-prod",
            "shipping-and-praying", "praying", "blaming-the-intern",
        ],
    },
    {
        "id": "sysadmin", "name": "Sysadmin", "category": "engineering", "rating": "mild",
        "desc": "it was DNS. it is always DNS.", "spinner": "dots", "theme": "matrix",
        "verbs": [
            "blaming-DNS", "confirming-it-was-DNS", "tailing-logs",
            "grepping-desperately", "bouncing-the-box", "rotating-certs",
            "chasing-a-cert-expiry", "draining-the-node", "cordoning-the-pod",
            "evicting-something", "scaling-horizontally", "scaling-out-of-budget",
            "paging-someone-else", "acknowledging-the-page", "snoozing-the-page",
            "ignoring-the-page", "writing-the-postmortem", "blamelessly-blaming",
            "updating-the-runbook", "not-updating-the-runbook", "clearing-the-cache",
            "turning-it-off-and-on", "checking-it-is-plugged-in", "ssh-ing-into-prod",
            "running-df--h", "deleting-old-logs", "freeing-inodes",
            "hunting-a-memory-leak", "restarting-nginx", "reloading-systemd",
            "editing-iptables-remotely", "locking-myself-out",
            "resetting-the-firewall", "waiting-for-DNS-propagation",
            "renewing-the-cert", "mounting-it-wrong", "fscking-it",
            "rebuilding-the-array", "holding-the-pager", "awake-at-3am",
        ],
    },
    {
        "id": "honest", "name": "Honest engineering", "category": "engineering",
        "rating": "sfw", "desc": "what the work actually is, most days",
        "spinner": "grow", "theme": "ice",
        "verbs": [
            "yak-shaving", "bikeshedding", "monkeypatching", "footgunning",
            "kludging", "refactoring", "rewriting", "reverting", "bisecting",
            "profiling", "benchmarking", "instrumenting", "tracing", "memoising",
            "denormalising", "normalising", "indexing", "sharding", "batching",
            "debouncing", "throttling", "caching", "invalidating-caches",
            "naming-things", "off-by-one-ing", "renaming-variables",
            "extracting-a-function", "inlining-a-function", "deleting-code",
            "deduplicating", "generalising-prematurely", "abstracting",
            "un-abstracting", "typing-it-properly", "casting-to-any",
            "widening-the-interface", "narrowing-the-type", "writing-a-test",
            "deleting-a-test", "mocking-everything", "stubbing", "fixturing",
            "seeding-data", "migrating", "backfilling", "feature-flagging",
            "dark-launching", "canarying", "rolling-forward",
        ],
    },
    {
        "id": "corporate", "name": "Corporate", "category": "work", "rating": "sfw",
        "desc": "circling back to socialise the deck", "spinner": "bar", "theme": "deep",
        "verbs": [
            "synergising", "circling-back", "boiling-the-ocean",
            "aligning-stakeholders", "socialising-the-deck", "leveraging-learnings",
            "right-sizing", "streamlining", "optimising-headcount", "ideating",
            "whiteboarding", "workshopping", "unpacking-that",
            "double-clicking-on-that", "taking-it-offline", "parking-it",
            "tabling-it", "actioning-it", "operationalising",
            "cascading-the-message", "drilling-down", "zooming-out",
            "moving-the-needle", "shifting-the-paradigm", "disrupting-nothing",
            "pivoting", "iterating", "sprinting", "grooming-the-backlog",
            "refining-the-backlog", "estimating-in-fibonacci", "re-estimating",
            "re-scoping", "de-scoping", "sunsetting", "deprecating",
            "reaching-out", "touching-base", "syncing-up",
            "aligning-on-alignment", "closing-the-loop", "owning-it",
            "running-it-up-the-flagpole", "picking-low-hanging-fruit",
        ],
    },
    {
        "id": "eldritch", "name": "Eldritch", "category": "fiction", "rating": "sfw",
        "desc": "something in the dependency tree is awake", "spinner": "noise",
        "theme": "void",
        "verbs": [
            "gibbering", "unnaming", "whispering-in-tongues", "communing",
            "awakening-something", "defiling-the-index", "screaming-into-the-void",
            "dreaming-in-R'lyeh", "transcribing-the-unspeakable",
            "birthing-eldritch-types", "unspooling-reality", "blaspheming",
            "invoking", "summoning", "banishing", "warding", "sealing-the-gate",
            "unsealing-the-gate", "reading-the-forbidden-docs",
            "glimpsing-the-schema", "going-mad-from-the-stack-trace",
            "counting-the-angles-wrong", "folding-space", "non-euclideaning",
            "tentacling", "writhing", "pulsing-wetly", "seeping", "congealing",
            "festering", "ascending", "descending", "becoming", "unbecoming",
            "forgetting-my-name", "remembering-too-much", "hearing-the-hum",
            "feeding-the-thing", "appeasing-it", "bargaining-with-it",
            "losing-the-bargain", "being-observed", "being-known", "being-consumed",
        ],
    },
    {
        "id": "pirate", "name": "Pirate", "category": "fiction", "rating": "sfw",
        "desc": "the rum is gone and so is the build", "spinner": "arc", "theme": "gold",
        "verbs": [
            "plunderin", "pillagin", "keelhaulin", "swabbin", "scuttlin",
            "parleyin", "mutinyin", "maroonin", "broadsidin", "boardin",
            "grapplin", "hoistin-the-colours", "strikin-the-colours",
            "weighin-anchor", "droppin-anchor", "trimmin-the-sails",
            "battenin-the-hatches", "splicin-the-mainbrace", "walkin-the-plank",
            "buryin-the-treasure", "diggin-for-treasure", "readin-the-map-wrong",
            "chartin-a-course", "runnin-aground", "takin-on-water", "bailin-out",
            "patchin-the-hull", "feedin-the-sharks", "singin-a-shanty",
            "drinkin-the-rum", "findin-the-rum-gone", "cursin-the-gods",
            "swearin-a-blood-oath", "hoardin-doubloons", "countin-doubloons",
            "losin-a-leg", "gettin-scurvy", "spottin-land", "missin-land",
            "sinkin",
        ],
    },
    {
        "id": "medieval", "name": "Medieval", "category": "fiction", "rating": "sfw",
        "desc": "the siege is going badly", "spinner": "triangle", "theme": "bone",
        "verbs": [
            "besieging", "sacking", "pillaging", "questing", "jousting", "smiting",
            "vanquishing", "parleying", "ransoming", "tithing",
            "selling-indulgences", "alchemising", "transmuting-lead", "bloodletting",
            "leeching", "plaguing", "flagellating", "excommunicating", "heresying",
            "inquisiting", "crusading", "pilgrimaging", "swearing-fealty",
            "breaking-an-oath", "siege-engineering", "trebucheting", "boiling-oil",
            "storming-the-keep", "holding-the-keep", "digging-a-moat",
            "filling-the-moat", "thatching", "ploughing", "harvesting", "famining",
            "feasting", "minstrelling", "jestering", "beheading",
            "succeeding-to-the-throne",
        ],
    },
    {
        "id": "hacker", "name": "Netrunner", "category": "fiction", "rating": "sfw",
        "desc": "jacked in, ice broken, logs wiped", "spinner": "binary",
        "theme": "matrix",
        "verbs": [
            "jacking-in", "netrunning", "ice-breaking", "flatlining-the-deck",
            "zero-daying", "rootkitting", "packet-sniffing", "port-scanning",
            "fuzzing", "pivoting-laterally", "escalating-privileges",
            "dropping-a-shell", "popping-a-box", "exfiltrating", "tunnelling",
            "spoofing", "poisoning-the-cache", "hijacking-a-session",
            "brute-forcing", "rainbow-tabling", "salting", "hashing", "cracking",
            "decompiling", "disassembling", "patching-the-binary",
            "hooking-a-syscall", "injecting", "overflowing-a-buffer",
            "smashing-the-stack", "chaining-gadgets", "bypassing-the-WAF",
            "evading", "going-dark", "burning-the-drive", "wiping-the-logs",
            "covering-tracks", "phoning-home", "beaconing", "ghosting",
        ],
    },
    {
        "id": "noir", "name": "Noir", "category": "fiction", "rating": "mild",
        "desc": "it was raining. it's always raining.", "spinner": "flip", "theme": "mono",
        "verbs": [
            "chain-smoking", "nursing-a-bourbon", "tailing-a-lead",
            "working-a-hunch", "canvassing", "staking-it-out", "dusting-for-prints",
            "sweating-the-perp", "leaning-on-a-witness", "following-the-money",
            "taking-the-case", "dropping-the-case", "getting-stonewalled",
            "getting-made", "getting-slugged", "coming-to-in-an-alley",
            "checking-the-ledger", "reading-the-file", "torching-the-file",
            "meeting-a-contact", "paying-an-informant", "doubting-the-client",
            "doubting-everyone", "seeing-through-the-dame",
            "falling-for-the-dame", "taking-the-fall", "cutting-a-deal",
            "calling-it-in", "filing-the-report", "burying-the-report",
            "staring-at-rain", "listening-to-the-radiator", "counting-the-days",
            "drinking-alone", "going-back-in", "knowing-better",
            "doing-it-anyway", "making-the-call", "wrapping-it-up",
        ],
    },
    {
        "id": "existential", "name": "Existential", "category": "feelings", "rating": "sfw",
        "desc": "one must imagine the compiler happy", "spinner": "circle",
        "theme": "void",
        "verbs": [
            "despairing", "absurding", "sisyphusing", "rolling-the-boulder",
            "staring-into-the-abyss", "being-stared-back-at",
            "questioning-the-premise", "doubting-the-self", "bracketing-the-world",
            "phenomenologising", "being-toward-death", "being-thrown",
            "falling-into-das-Man", "acting-in-bad-faith", "choosing-anyway",
            "nauseating", "vertigo-ing", "dreading-freedom", "meaning-making",
            "meaning-unmaking", "eternally-recurring", "amor-fati-ing",
            "willing-to-power", "revaluating-values", "watching-cave-shadows",
            "living-unexamined", "cogito-ing", "doubting-the-doubt",
            "arranging-monads", "categorically-imperativing", "groping-at-noumena",
            "dialecticising", "alienating-labour", "reifying", "deconstructing",
            "differing-and-deferring", "simulacrum-ing", "hyperreal-ing",
            "refusing-the-question",
        ],
    },
    {
        "id": "chef", "name": "Kitchen", "category": "work", "rating": "sfw",
        "desc": "yes chef. behind. corner. hot pan.", "spinner": "half", "theme": "ember",
        "verbs": [
            "julienning", "brunoising", "chiffonading", "deglazing", "reducing",
            "emulsifying", "tempering", "blanching", "shocking", "searing",
            "basting", "braising", "confiting", "sous-viding", "resting-the-meat",
            "seasoning-to-taste", "over-seasoning", "under-seasoning",
            "burning-the-roux", "saving-the-roux", "mounting-with-butter",
            "clarifying", "skimming", "straining", "plating-up", "wiping-the-rim",
            "garnishing", "tasting", "tasting-again", "yes-cheffing",
            "firing-the-pass", "calling-the-order", "dropping-the-pass",
            "sending-it-back", "eighty-sixing-it", "prepping", "mise-en-placing",
            "breaking-down-a-chicken", "sharpening-knives", "cutting-myself",
        ],
    },
    {
        "id": "wizard", "name": "Wizard", "category": "fiction", "rating": "sfw",
        "desc": "a wizard is never late", "spinner": "star", "theme": "vapor",
        "verbs": [
            "incanting", "hexing", "scrying", "transmuting", "banishing",
            "conjuring", "summoning-a-familiar", "feeding-the-familiar",
            "memorising-spells", "forgetting-spells", "preparing-spells",
            "burning-a-slot", "channelling", "burning-mana", "over-channelling",
            "backfiring", "misfiring", "polymorphing", "polymorphing-badly",
            "enchanting", "disenchanting", "cursing", "uncursing", "warding",
            "dispelling", "counterspelling", "teleporting", "mis-teleporting",
            "divining", "prophesying", "misreading-a-prophecy",
            "brewing-a-potion", "mislabelling-a-potion",
            "drinking-the-wrong-potion", "consulting-the-tome", "losing-the-tome",
            "growing-the-beard", "stroking-the-beard", "arriving-precisely-on-time",
        ],
    },
    {
        "id": "commentator", "name": "Commentator", "category": "work", "rating": "sfw",
        "desc": "absolute scenes in the terminal", "spinner": "arrows", "theme": "acid",
        "verbs": [
            "going-for-it", "sending-it", "absolutely-sending-it",
            "parking-the-bus", "pressing-high", "sitting-deep",
            "playing-out-from-the-back", "hoofing-it-clear", "hitting-a-screamer",
            "dragging-it-wide", "rattling-the-woodwork", "going-down-easy",
            "rolling-around", "appealing-for-it", "checking-the-VAR",
            "overturning-it", "upholding-it", "booking-someone", "seeing-red",
            "taking-one-for-the-team", "running-the-channels",
            "making-the-overlap", "whipping-it-in", "glancing-it-on",
            "nodding-it-home", "tapping-it-in", "shanking-it", "ballooning-it",
            "wasting-time", "running-down-the-clock", "blowing-the-whistle",
            "adding-eight-minutes", "snatching-it-late", "absolute-scenes",
            "bedlam", "pandemonium", "limbs-everywhere", "one-for-the-ages",
        ],
    },
    {
        "id": "shakespeare", "name": "Shakespearean", "category": "rude", "rating": "mild",
        "desc": "thou art erroring, and most foully", "spinner": "layer", "theme": "gold",
        "verbs": [
            "beshrewing", "bewailing", "cozening", "gulling", "prating", "railing",
            "chiding", "upbraiding", "importuning", "dissembling", "forswearing",
            "forsoothing", "prithee-debugging", "thou-art-erroring",
            "hie-ing-thee-hence", "avaunting", "fie-ing", "zounding", "anon-ing",
            "wherefore-arting", "doth-protesting", "methinksing",
            "perchance-dreaming", "muttering-asides", "soliloquising",
            "monologuing", "exeunting", "exiting-pursued-by-a-bear",
            "star-crossing", "cuckolding", "wenching", "bawding", "knaving",
            "varleting", "bewailing-a-whoreson-build", "canker-blossoming",
            "ill-favouring", "pox-wishing", "plaguing-both-houses",
        ],
    },
    {
        "id": "genz", "name": "Gen Z", "category": "feelings", "rating": "mild",
        "desc": "let him cook", "spinner": "hearts", "theme": "vapor",
        "verbs": [
            "cooking", "letting-him-cook", "no-capping", "capping", "rizzing-up",
            "negative-rizzing", "glazing", "yapping", "mogging", "looksmaxxing",
            "aura-farming", "losing-aura", "delulu-ing", "brainrotting",
            "doomscrolling", "ratioing", "getting-ratioed", "sending-it",
            "being-so-real", "eating-it-up", "leaving-no-crumbs",
            "understanding-the-assignment", "missing-the-assignment",
            "touching-grass", "refusing-grass", "gatekeeping", "girlbossing",
            "main-charactering", "side-questing", "NPC-ing", "lore-dropping",
            "canon-eventing", "hard-launching", "soft-launching", "ghosting",
            "breadcrumbing", "situationshipping", "shipping-it-mid",
            "low-key-panicking",
        ],
    },
    {
        "id": "gymbro", "name": "Gym", "category": "work", "rating": "mild",
        "desc": "just one more set", "spinner": "switch", "theme": "ember",
        "verbs": [
            "repping-out", "maxing-out", "PR-ing", "failing-the-lift",
            "ego-lifting", "half-repping", "cheat-repping", "chasing-the-pump",
            "bulking", "dirty-bulking", "cutting", "recomping", "hitting-legs",
            "skipping-leg-day", "mirror-checking", "flexing", "mogging",
            "getting-mogged", "loading-plates", "unloading-plates",
            "re-racking-weights", "not-re-racking-weights",
            "curling-in-the-squat-rack", "grunting", "chalking-up",
            "tightening-the-belt", "wrapping-wrists", "shaking-the-pre-workout",
            "counting-macros", "protein-maxxing", "loading-creatine", "deloading",
            "overtraining", "resting-90-seconds", "resting-15-minutes",
            "filming-the-set", "deleting-the-set", "asking-for-a-spot",
            "refusing-a-spot",
        ],
    },
    {
        "id": "zen", "name": "Zen", "category": "feelings", "rating": "sfw",
        "desc": "the build is neither late nor early", "spinner": "static",
        "theme": "bone",
        "verbs": [
            "breathing", "sitting-with-it", "noticing", "observing", "allowing",
            "releasing", "non-attaching", "non-doing", "non-striving",
            "letting-go", "letting-be", "returning-to-the-breath",
            "counting-breaths", "losing-count", "beginning-again", "softening",
            "unclenching", "dropping-the-shoulders", "arriving", "being-here",
            "being-now", "accepting-what-is", "holding-it-lightly",
            "watching-thoughts-pass", "not-following-them",
            "resting-in-awareness", "dissolving", "emptying", "sweeping-the-path",
            "carrying-water", "chopping-wood", "ringing-the-bell", "bowing",
            "sitting-again", "still-sitting",
        ],
    },
    {
        "id": "chaos", "name": "Chaos", "category": "meta", "rating": "nsfw",
        "desc": "a shuffled sample of every pack at once", "spinner": "noise",
        "theme": "rainbow", "kind": "meta",
    },
    {
        "id": "news", "name": "Live news", "category": "meta", "rating": "sfw",
        "desc": "the latest wire headlines, refreshed in the background",
        "spinner": "earth", "theme": "ice", "kind": "live", "url": None,
    },
]

CATEGORIES = ("rude", "engineering", "work", "fiction", "feelings", "meta")


# ---------------------------------------------------------------------------
# Pack registry
# ---------------------------------------------------------------------------

def _index(packs):
    return dict((p["id"], p) for p in packs)


def validate_pack(raw, source="<memory>"):
    """Check a user-supplied pack dict. Returns (pack, messages).

    Never raises — a malformed file in ~/.claude/spinner-packs is reported and
    skipped, not turned into a crash on startup. Two severities, because they
    deserve different outcomes: a pack with no id or no verbs is unusable and
    `pack` comes back None, but one bad verb or an unknown theme name only
    costs you that verb or a fallback theme. Rejecting a 200-verb pack over a
    single over-long entry would be obnoxious. Every message is surfaced either
    way, so nothing is silently dropped.
    """
    notes, fatal = [], []
    if not isinstance(raw, dict):
        return None, ["%s: not a JSON object" % source]

    pid = str(raw.get("id", "")).strip()
    if not pid:
        fatal.append("%s: missing 'id'" % source)
    elif not re.match(r"^[a-z0-9][a-z0-9_-]*$", pid):
        fatal.append("%s: id %r must be lowercase letters, digits, - and _"
                     % (source, pid))

    verbs_raw = raw.get("verbs")
    verbs = []
    if not isinstance(verbs_raw, list) or not verbs_raw:
        fatal.append("%s: 'verbs' must be a non-empty list" % source)
    else:
        for v in verbs_raw:
            if not isinstance(v, str):
                notes.append("%s: skipped verb %r — not a string" % (source, v))
                continue
            v = v.strip()
            if not v:
                continue
            if disp_width(v) > SPINNER_MAX:
                notes.append("%s: skipped verb %r — wider than %d columns"
                             % (source, v, SPINNER_MAX))
                continue
            verbs.append(v)
        if not verbs:
            fatal.append("%s: no usable verbs" % source)

    rating = str(raw.get("rating", "sfw")).lower()
    if rating not in RATINGS:
        notes.append("%s: rating %r is not one of %s — using sfw"
                     % (source, rating, "/".join(RATINGS)))
        rating = "sfw"

    spinner = str(raw.get("spinner", DEFAULT_SPINNER))
    if spinner not in SPINNERS:
        notes.append("%s: unknown animation %r — using %s"
                     % (source, spinner, DEFAULT_SPINNER))
        spinner = DEFAULT_SPINNER
    theme = str(raw.get("theme", DEFAULT_THEME))
    if theme not in THEMES:
        notes.append("%s: unknown theme %r — using %s"
                     % (source, theme, DEFAULT_THEME))
        theme = DEFAULT_THEME

    if fatal:
        return None, fatal + notes
    return {
        "id": pid,
        "name": str(raw.get("name") or pid.replace("-", " ").title()),
        "desc": str(raw.get("desc") or raw.get("description") or "a custom pack"),
        "category": str(raw.get("category") or "custom"),
        "rating": rating,
        "spinner": spinner,
        "theme": theme,
        "verbs": verbs,
        "custom": True,
    }, notes


def load_user_packs(dirpath=None):
    """Load every ~/.claude/spinner-packs/*.json. Returns (packs, errors)."""
    dirpath = USER_PACK_DIR if dirpath is None else dirpath
    packs, errors = [], []
    for path in sorted(glob.glob(os.path.join(dirpath, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError) as e:
            errors.append("%s: %s" % (os.path.basename(path), e))
            continue
        for item in (raw if isinstance(raw, list) else [raw]):
            pack, errs = validate_pack(item, os.path.basename(path))
            if pack:
                packs.append(pack)
            errors.extend(errs)
    return packs, errors


_USER_PACKS = None
_USER_ERRORS = []


def all_packs(include_user=True, refresh=False):
    """Built-in packs plus validated user packs. A user pack whose id collides
    with a built-in wins — that's the documented way to override one."""
    global _USER_PACKS, _USER_ERRORS
    if not include_user:
        return list(PACKS)
    if _USER_PACKS is None or refresh:
        _USER_PACKS, _USER_ERRORS = load_user_packs()
    merged = _index(PACKS)
    for p in _USER_PACKS:
        merged[p["id"]] = p
    order = [merged[p["id"]] for p in PACKS]
    order += [p for p in _USER_PACKS if p["id"] not in _index(PACKS)]
    return order


def user_pack_errors():
    all_packs()  # force the load
    return list(_USER_ERRORS)


def get_pack(pid, packs=None):
    """Resolve a pack by exact id, then by unique prefix, then None."""
    packs = all_packs() if packs is None else packs
    idx = _index(packs)
    if pid in idx:
        return idx[pid]
    hits = [p for p in packs if p["id"].startswith(pid)]
    return hits[0] if len(hits) == 1 else None


def resolve_verbs(pack, url=None, packs=None):
    """The word list for any pack, static, meta or live.

    One entry point so callers never branch on pack kind — adding a live pack
    later means adding a kind here, not another if/else at each call site.
    Live packs return [] when the feed is unreachable; callers report that.
    """
    kind = pack.get("kind", "static")
    if kind == "live":
        items = fetch_news(url or pack.get("url") or NEWS_URL)
        # Condense (not truncate) each title to the spinner's 56-col line so the
        # whole headline reads instead of dangling behind an ellipsis.
        return [condense(i["title"], SPINNER_MAX) for i in items]
    if kind == "meta":
        pool = []
        for p in (all_packs() if packs is None else packs):
            if p.get("kind", "static") == "static":
                pool.extend(p["verbs"])
        seen, out = set(), []
        for v in stable_order(pack["id"], pool):
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out[:120]
    return list(pack.get("verbs", []))


def sample_verbs(pack, url=None):
    """Verbs safe to animate in a preview: never hits the network."""
    if pack.get("kind") == "live":
        return ["fetching the wire…", "live headlines", "refreshed every 15s"]
    return resolve_verbs(pack, url)


def verb_at(pack, now, phase=0.0, verbs=None):
    """Which sample verb is showing at time `now` — pure, like frame_at()."""
    pool = verbs if verbs else sample_verbs(pack)
    if not pool:
        return ""
    order = stable_order(pack["id"], pool)
    return order[int((now + phase) / VERB_DWELL) % len(order)]


# ---------------------------------------------------------------------------
# Headline condensing (news packs)
# ---------------------------------------------------------------------------

# Trailing source credit after a dash/pipe — drop it, the wire is the wire.
_SOURCE_TAG = re.compile(
    r"\s*[—–|]\s*(reuters|bloomberg|cnbc|ap|afp|wsj|ft|financial times|bbc|cnn|"
    r"the guardian|nyt|new york times|barron'?s|marketwatch|axios|politico)\s*$",
    re.I,
)
_PARENS = re.compile(r"\s*[(\[][^)\]]*[)\]]")        # "(details)" / "[tag]"
_WS = re.compile(r"\s+")
_ARTICLES = re.compile(r"\b(?:a|an|the)\s+", re.I)   # headline register omits these
_CLAUSE = re.compile(r"[—–,:;]")                     # NOT '-' (lives inside words)
# Stock long word -> standard short form. Word-boundaried so we never eat a
# substring (e.g. "millionaire").
_ABBREV = [
    (re.compile(r"\bpercent\b", re.I), "%"),
    (re.compile(r"\bversus\b", re.I), "vs"),
    (re.compile(r"\bUnited States\b"), "US"),
    (re.compile(r"\bUnited Kingdom\b"), "UK"),
    (re.compile(r"\bEuropean Union\b"), "EU"),
    (re.compile(r"\bbillion\b", re.I), "bn"),
    (re.compile(r"\bmillion\b", re.I), "mn"),
    (re.compile(r"\btrillion\b", re.I), "tn"),
    (re.compile(r"\bgovernment\b", re.I), "govt"),
]


def condense(title, limit=SPINNER_MAX):
    """Shorten a headline to <= limit DISPLAY columns while keeping it readable
    as a whole thought — the opposite of fit(), which hard-truncates and hides
    the tail behind an ellipsis. Steps escalate only as far as needed to fit:

      1. tidy whitespace, strip a trailing source tag and any parentheticals
      2. swap stock long words for standard short forms (percent -> %)
      3. drop articles (headline register omits them anyway)
      4. keep the leading clause that still fits (headlines front-load the fact)
      5. trim whole words off the end; ellipsis only if one word already overflows

    Measured in display columns (disp_width), so CJK/emoji headlines fit honestly.
    """
    t = _PARENS.sub("", _SOURCE_TAG.sub("", _WS.sub(" ", str(title)).strip())).strip()
    if disp_width(t) <= limit:
        return t

    for pat, rep in _ABBREV:
        t = pat.sub(rep, t)
    t = _WS.sub(" ", t).strip()
    if disp_width(t) <= limit:
        return t

    stripped = _WS.sub(" ", _ARTICLES.sub("", t)).strip()
    if stripped and disp_width(stripped) <= limit:
        return stripped
    t = stripped or t

    # 4. leading clause: the longest head up to a clause break that still fits.
    best = ""
    for m in _CLAUSE.finditer(t):
        head = t[:m.start()].rstrip(" —–,:;")
        if disp_width(best) < disp_width(head) <= limit:
            best = head
    if best:
        return best

    # 5. whole-word trim from the end — no mid-word cuts.
    words = t.split(" ")
    while words and disp_width(" ".join(words)) > limit:
        words.pop()
    if words:
        return " ".join(words)

    # 6. last resort: a single word longer than the whole line. Hard-cut with an
    # ellipsis, reserving one column for it.
    kept, cols = "", 0
    for ch in t:
        w = char_width(ch)
        if cols + w > limit - 1:
            break
        kept, cols = kept + ch, cols + w
    return (kept + "…") if kept else t[:1]


# ---------------------------------------------------------------------------
# The live feed
# ---------------------------------------------------------------------------

def normalize_items(raw):
    """Normalise feed items to [{title, summary}]. Accepts plain strings (the
    world feed) or {title/summary/description} objects (the markets feed carries
    a summary so the ticker can show it when you press n). Drops empty titles."""
    out = []
    for it in raw or []:
        if it is None:
            continue          # str(None) is "None", which is not a headline
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
    parts.append("\n\n\033[2m— press any key to resume —\033[0m\n")
    _write("".join(parts))
    _read_key(timeout=None)  # block until any key


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

def _write(s):
    """Write and flush, treating a closed pipe as a clean exit.

    `./spin.py --verbs | head` used to die with an unhandled BrokenPipeError
    traceback. main() also sets SIGPIPE back to the default where the platform
    has one, so the common case exits silently like any other Unix filter; this
    is the belt-and-braces path for platforms that don't (Windows)."""
    try:
        sys.stdout.write(s)
        sys.stdout.flush()
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        raise SystemExit(0)


def spin(interval=0.6, pack=None, spinner=None, theme=None, mode=None, url=None):
    """The standalone animation: one pack, cycled on a timer.

    Default cadence is fast so you actually see the verbs go by — the real
    Claude Code spinner only swaps a verb when a new operation starts, so most
    never show.
    """
    pack = pack or get_pack("profanity")
    sid = spinner or pack.get("spinner", DEFAULT_SPINNER)
    tid = theme or pack.get("theme", DEFAULT_THEME)
    mode = color_mode() if mode is None else mode
    sp = get_spinner(sid)
    verbs = resolve_verbs(pack, url)
    if not verbs:
        _write("nothing to spin in pack %r\n" % pack["id"])
        return
    bag = shuffle_bag(verbs)
    verb = next(bag)
    prefix = frame_width(sid) + 1
    frames_per_verb = max(1, round(interval / sp["interval"]))
    try:
        for i, frame in enumerate(itertools.cycle(sp["frames"])):
            if i % frames_per_verb == 0:
                verb = next(bag)
            width = shutil.get_terminal_size((80, 20)).columns
            phase = (i % len(sp["frames"])) / float(len(sp["frames"]))
            line = render_line(frame, fit(verb, width, prefix), tid, mode, phase)
            _write("\r\033[K" + line + " ")
            time.sleep(sp["interval"])
    except KeyboardInterrupt:
        pass
    finally:
        _write("\r\033[K")


def spin_news(interval, url, spinner="earth", theme="ice", mode=None):
    """The live ticker: headlines instead of verbs, with n to read the story."""
    mode = color_mode() if mode is None else mode
    sp = get_spinner(spinner)
    prefix = frame_width(spinner) + 1
    feed = NewsFeed(url)
    if not feed.items:
        _write("\r\033[Kno feed at %s — set SPIN_NEWS_URL or pass --news-url\n" % url)
        return
    snapshot = tuple(feed.items)
    gen = shuffle_bag(list(snapshot))
    line = next(gen)
    frames_per_line = max(1, round(interval / sp["interval"]))

    # Interactive keys only work on a real terminal. cbreak mode lets us read one
    # keystroke at a time without waiting for Enter, and leaves Ctrl-C working.
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    old_term = fd = None
    if interactive:
        import termios, tty
        fd = sys.stdin.fileno()
        old_term = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        _write("\033[2m[n] read summary   [q] quit\033[0m\n")

    try:
        for i, frame in enumerate(itertools.cycle(sp["frames"])):
            if i % frames_per_line == 0:
                current = tuple(feed.items)
                # Rebuild the deck only when the headlines ACTUALLY changed
                # (compare by value, not identity, or every refresh resets it).
                if current and current != snapshot:
                    snapshot = current
                    gen = shuffle_bag(list(current))
                line = next(gen)
            width = shutil.get_terminal_size((80, 20)).columns
            phase = (i % len(sp["frames"])) / float(len(sp["frames"]))
            _write("\r\033[K" + render_line(
                frame, fit(line["title"], width, prefix), theme, mode, phase, ""))
            time.sleep(sp["interval"])
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
        _write("\r\033[K")


def wall(theme=None, mode=None, duration=None):
    """Every animation running at once, in a grid. The showreel.

    Plain ANSI rather than curses so it works piped into `script`, in a CI log,
    or anywhere curses can't initialise — it just repaints N lines in place.
    """
    mode = color_mode() if mode is None else mode
    ids = list(SPINNERS)
    width = shutil.get_terminal_size((80, 24)).columns
    label_w = max(disp_width(SPINNERS[s]["name"]) for s in ids) + 2
    cell = label_w + 8
    cols = max(1, min(4, width // cell))
    rows = (len(ids) + cols - 1) // cols
    start = time.monotonic()
    hidden = mode != "none"
    if hidden:
        _write("\033[?25l")  # hide the cursor; the finally block puts it back
    _write("every animation, all at once — Ctrl-C to stop\n")
    try:
        first = True
        while True:
            now = time.monotonic() - start
            if duration is not None and now >= duration:
                break
            if not first:
                _write("\033[%dA" % rows)  # rewind to the top of the grid
            first = False
            out = []
            for r in range(rows):
                parts = []
                for c in range(cols):
                    i = c * rows + r
                    if i >= len(ids):
                        continue
                    sid = ids[i]
                    frame = frame_at(sid, now, phase=i * 0.37)
                    ph = (now / get_spinner(sid)["interval"]) % 1.0
                    glyph = frame
                    if mode != "none":
                        glyph = sgr(theme_rgb(theme or "rainbow", "frame",
                                              (i / len(ids) + now / 6.0)), mode,
                                    bold=True) + frame + RESET
                    parts.append(glyph + " " + pad(SPINNERS[sid]["name"], label_w))
                out.append("\033[K" + "  ".join(parts).rstrip() + "\n")
            _write("".join(out))
            time.sleep(TICK)
    except KeyboardInterrupt:
        pass
    finally:
        if hidden:
            _write("\033[?25h")


def preview_block(pack, spinner=None, theme=None, mode=None, url=None, n=6):
    """A static, non-animated sample of a pack — what --list --verbose prints
    and what the gallery's detail pane shows. No timers, no escape juggling."""
    mode = color_mode() if mode is None else mode
    sid = spinner or pack.get("spinner", DEFAULT_SPINNER)
    tid = theme or pack.get("theme", DEFAULT_THEME)
    frames = get_spinner(sid)["frames"]
    verbs = sample_verbs(pack, url)[:n] or ["(empty)"]
    lines = []
    for i, v in enumerate(verbs):
        lines.append(render_line(frames[i % len(frames)], v, tid, mode,
                                 i / float(max(1, len(verbs)))))
    return lines


# ---------------------------------------------------------------------------
# Gallery state — pure, no curses, so it can be unit-tested headlessly
# ---------------------------------------------------------------------------

class GalleryState:
    """Everything the gallery knows, with none of the drawing.

    The curses layer is a renderer over this object: it asks what to show and
    forwards keystrokes. Keeping the two apart is what makes filtering, search,
    selection and even the animation clock testable without a terminal.
    """

    def __init__(self, packs=None, sfw=False, spinner=None, theme=None):
        self.packs = list(packs if packs is not None else all_packs())
        self.query = ""
        self.category = None          # None = every category
        self.sfw = bool(sfw)
        self.spinner_override = spinner
        self.theme_override = theme
        self.idx = 0
        self.status = ""
        self.mode = "list"            # list | wall | help
        self._cats = [None] + [c for c in CATEGORIES
                               if any(p.get("category") == c for p in self.packs)]
        extra = sorted(set(p.get("category") for p in self.packs)
                       - set(CATEGORIES) - {None})
        self._cats += extra

    # -- filtering ---------------------------------------------------------
    def filtered(self):
        q = self.query.lower().strip()
        out = []
        for p in self.packs:
            if self.sfw and p.get("rating") != "sfw":
                continue
            if self.category is not None and p.get("category") != self.category:
                continue
            if q:
                hay = " ".join([p["id"], p.get("name", ""), p.get("desc", ""),
                                p.get("category", "")] + list(p.get("verbs", [])))
                if q not in hay.lower():
                    continue
            out.append(p)
        return out

    def current(self):
        rows = self.filtered()
        if not rows:
            return None
        self.idx = max(0, min(self.idx, len(rows) - 1))
        return rows[self.idx]

    # -- navigation --------------------------------------------------------
    def move(self, n):
        rows = self.filtered()
        if rows:
            self.idx = (self.idx + n) % len(rows)

    def set_query(self, q):
        self.query = q
        self.idx = 0

    def cycle_category(self, step=1):
        i = self._cats.index(self.category) if self.category in self._cats else 0
        self.category = self._cats[(i + step) % len(self._cats)]
        self.idx = 0

    def toggle_sfw(self):
        self.sfw = not self.sfw
        self.idx = 0

    def cycle_spinner(self, step=1):
        """Cycle the animation override, passing through None (= pack default)."""
        opts = [None] + list(SPINNERS)
        i = opts.index(self.spinner_override) if self.spinner_override in opts else 0
        self.spinner_override = opts[(i + step) % len(opts)]

    def cycle_theme(self, step=1):
        opts = [None] + list(THEMES)
        i = opts.index(self.theme_override) if self.theme_override in opts else 0
        self.theme_override = opts[(i + step) % len(opts)]

    def randomise(self):
        rows = self.filtered()
        if rows:
            self.idx = random.randrange(len(rows))
        self.spinner_override = random.choice(list(SPINNERS))
        self.theme_override = random.choice(list(THEMES))

    # -- resolution --------------------------------------------------------
    def spinner_for(self, pack):
        return self.spinner_override or pack.get("spinner", DEFAULT_SPINNER)

    def theme_for(self, pack):
        return self.theme_override or pack.get("theme", DEFAULT_THEME)

    def frame_for(self, pack, now, phase=0.0):
        return frame_at(self.spinner_for(pack), now, phase)

    def verb_for(self, pack, now, phase=0.0):
        return verb_at(pack, now, phase)

    def selection(self):
        """What --set would apply, as a plain dict."""
        p = self.current()
        if p is None:
            return None
        return {"pack": p["id"], "spinner": self.spinner_for(p),
                "theme": self.theme_for(p)}


# ---------------------------------------------------------------------------
# The gallery
# ---------------------------------------------------------------------------
# The layout is written against two tiny interfaces — a screen that can
# getmaxyx/addstr/erase, and an attribute set — so the exact same drawing code
# renders to a curses window or to a plain text grid. That's what lets the TUI
# be screenshotted and asserted in CI without a terminal, instead of being the
# one part of the program nothing can test.

HELP_LINES = [
    ("↑ ↓  j k", "move between packs"),
    ("enter", "apply this pack to Claude Code"),
    ("/", "search packs and verbs (enter accepts, esc clears)"),
    ("tab", "cycle the category filter"),
    ("s", "toggle the safe-for-work filter"),
    ("f  F", "cycle the animation forwards / back"),
    ("t  T", "cycle the colour theme forwards / back"),
    ("w", "wall mode — every animation at once"),
    ("r", "randomise pack, animation and theme"),
    ("u", "restore the spinner you had before"),
    ("?", "this help"),
    ("q", "quit without changing anything"),
]


class Attrs:
    """Text attributes. The plain-text backend renders everything unstyled."""

    BOLD = DIM = REVERSE = NORMAL = 0

    def color(self, rgb, bold=False):
        return 0


class CursesAttrs(Attrs):
    """Curses attributes, with lazily allocated colour pairs.

    Pairs are a scarce, terminal-dependent resource: a 16-colour xterm offers
    64 of them, and asking for pair 200 raises curses.error and takes the whole
    app down. So allocate on demand, cap at what the terminal reports, and fall
    back to bold/plain once they run out rather than failing.
    """

    def __init__(self, curses_mod):
        c = self.curses = curses_mod
        self.BOLD, self.DIM = c.A_BOLD, c.A_DIM
        self.REVERSE, self.NORMAL = c.A_REVERSE, c.A_NORMAL
        self.pairs = {}
        self.next = 1
        self.ok = False
        self.colors = 0
        self.max_pairs = 0
        self.bg = 0
        try:
            c.start_color()
            try:
                c.use_default_colors()
                self.bg = -1
            except Exception:
                self.bg = 0
            self.colors = c.COLORS
            self.max_pairs = min(c.COLOR_PAIRS, 256)
            self.ok = self.colors >= 8
        except Exception:
            self.ok = False

    def color(self, rgb, bold=False):
        base = self.BOLD if bold else 0
        if not self.ok:
            return base
        idx = rgb_to_256(rgb) if self.colors >= 256 else rgb_to_16(rgb)
        idx = min(idx, self.colors - 1)
        if idx not in self.pairs:
            if self.next >= self.max_pairs:
                return base          # out of pairs: plain or bold, but drawn
            try:
                self.curses.init_pair(self.next, idx, self.bg)
            except Exception:
                return base
            self.pairs[idx] = self.next
            self.next += 1
        return self.curses.color_pair(self.pairs[idx]) | base


class TextScreen:
    """A curses-window lookalike that draws into a character grid.

    Only the three methods the layout actually uses. Wide glyphs claim the
    following cell so nothing overlaps them, exactly as a terminal would.
    """

    def __init__(self, height, width):
        self.h, self.w = height, width
        self.erase()

    def getmaxyx(self):
        return self.h, self.w

    def erase(self):
        self.grid = [[" "] * self.w for _ in range(self.h)]

    def refresh(self):
        pass

    def addstr(self, y, x, text, attr=0):
        if not (0 <= y < self.h):
            return
        for ch in text:
            cw = char_width(ch)
            if cw == 0:                      # combining mark joins the cell left
                if x - 1 >= 0 and x - 1 < self.w:
                    self.grid[y][x - 1] += ch
                continue
            if x >= self.w:
                break
            self.grid[y][x] = ch
            for k in range(1, cw):
                if x + k < self.w:
                    self.grid[y][x + k] = ""
            x += cw

    def text(self):
        return "\n".join("".join(row).rstrip() for row in self.grid)


def _put(win, y, x, text, attr=0, maxw=None):
    """Draw clipped to the window, measured in DISPLAY columns, never raising.

    curses raises on a write that would touch the bottom-right cell, and a
    narrow window otherwise turns every redraw into a crash. Everything the
    gallery draws goes through here.
    """
    try:
        h, w = win.getmaxyx()
    except Exception:
        return
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    limit = w - x - 1
    if maxw is not None:
        limit = min(limit, maxw)
    if limit <= 0:
        return
    if disp_width(text) > limit:
        text = fit(text, limit + 1, prefix=0)
    try:
        win.addstr(y, x, text, attr)
    except Exception:
        pass


# Frames run from one column ("|") to five ("▰▱▱▱▱") and every row animates
# independently, so the glyph gets a fixed-width field. Without it each row's
# name would start at a different column and the list would look broken.
GLYPH_W = max(frame_width(s) for s in SPINNERS)


def _row_skew(pack_id):
    """A stable per-pack clock offset, so the list shimmers instead of marching
    in lockstep. Stable across runs — hash() is salted per process."""
    return (sum(map(ord, pack_id)) % 7) * 0.31


def draw_row(scr, at, y, x, st, pack, now, selected, w):
    """One list row. `x` is the left edge, `w` the columns it may use."""
    sid, tid = st.spinner_for(pack), st.theme_for(pack)
    phase = (now / get_spinner(sid)["interval"]) % 1.0
    skew = _row_skew(pack["id"])
    frame = st.frame_for(pack, now, phase=skew)
    base = at.REVERSE if selected else 0

    namew = max(8, min(22, w - GLYPH_W - 14))
    col_mark, col_glyph = x, x + 1
    col_name = col_glyph + GLYPH_W + 1
    col_badge = col_name + namew + 1
    col_verb = col_badge + 2
    room = x + w - col_verb

    _put(scr, y, col_mark, "▌" if selected else " ",
         at.color(theme_rgb(tid, "frame", phase), True))
    # Right-aligned in the field so the glyph always sits beside the name,
    # whether it's one column of braille or five of progress bar.
    _put(scr, y, col_glyph, rpad(frame, GLYPH_W),
         at.color(theme_rgb(tid, "frame", phase), True) | base, maxw=GLYPH_W)
    _put(scr, y, col_name, pad(pack.get("name", pack["id"]), namew),
         at.color(theme_rgb(tid, "text"), selected) | base, maxw=namew)
    _put(scr, y, col_badge,
         {"sfw": " ", "mild": "~", "nsfw": "!"}.get(pack.get("rating"), " "),
         at.DIM | base)
    if room > 6:
        _put(scr, y, col_verb, st.verb_for(pack, now, phase=skew * 1.7) + "…",
             (0 if selected else at.DIM) | base, maxw=room)


def draw_detail(scr, at, y0, x0, w, h, st, pack, now):
    """The right-hand pane: what this pack is, and how it will really look."""
    if pack is None:
        _put(scr, y0, x0, "no packs match", at.DIM)
        return
    sid, tid = st.spinner_for(pack), st.theme_for(pack)
    verbs = sample_verbs(pack)
    y = y0
    _put(scr, y, x0, pack.get("name", pack["id"]), at.BOLD)
    y += 1
    _put(scr, y, x0, pack.get("desc", ""), at.DIM)
    y += 2
    meta = "%s · %s · %s" % (pack.get("category", "?"), pack.get("rating", "?"),
                             plural(len(verbs), "verb"))
    if pack.get("custom"):
        meta += " · custom"
    _put(scr, y, x0, meta, at.DIM)
    y += 1
    _put(scr, y, x0, "%s · %s" % (get_spinner(sid)["name"], get_theme(tid)["name"]),
         at.DIM)
    y += 2
    # A box exactly as wide as the line Claude Code renders, so you can see at a
    # glance which verbs come close to being chopped at SPINNER_MAX columns.
    # Needs the header line, both rules and at least one verb — in a window too
    # short for that, show nothing rather than an empty box.
    room = min(len(verbs), y0 + h - y - 4)
    if room < 1:
        return
    _put(scr, y, x0, "as Claude Code will draw it", at.DIM)
    y += 1
    inner = max(8, min(SPINNER_MAX, w - 2))
    rule = "─" * inner
    _put(scr, y, x0, "┌" + rule + "┐", at.DIM)
    y += 1
    # Claude Code draws ITS OWN glyph — there's no setting for it — so this box
    # shows braille whatever animation you're previewing. The colour, though, is
    # genuinely what you'll get: that's the `claude` token we write.
    real = CLAUDE_SPINNER
    fw = frame_width(real)
    for i in range(room):
        v = verbs[(int(now / VERB_DWELL) + i) % len(verbs)]
        ph = (now / get_spinner(real)["interval"] + i * 0.2) % 1.0
        _put(scr, y, x0, "│", at.DIM)
        _put(scr, y, x0 + 1, frame_at(real, now, i * 0.4),
             at.color(theme_rgb(tid, "frame", ph), True), maxw=fw)
        _put(scr, y, x0 + 2 + fw, fit(v + "…", inner - fw - 1, prefix=0),
             at.color(theme_rgb(tid, "text")), maxw=inner - fw - 2)
        _put(scr, y, x0 + inner + 1, "│", at.DIM)
        y += 1
    _put(scr, y, x0, "└" + rule + "┘", at.DIM)
    y += 1
    # Say plainly which half of the selection actually leaves this program.
    _put(scr, y, x0, "real: verbs + colour · local: %s animation"
         % get_spinner(sid)["name"].lower(), at.DIM)


def draw_wall(scr, at, st, now, h, w):
    """Every animation running at once."""
    ids = list(SPINNERS)
    label_w = max(disp_width(SPINNERS[s]["name"]) for s in ids)
    cell = label_w + GLYPH_W + 3
    cols = max(1, min(6, (w - 4) // cell))
    rows = (len(ids) + cols - 1) // cols
    _put(scr, 0, 2, "every animation, all at once", at.BOLD)
    _put(scr, 1, 2, "w or esc to go back", at.DIM)
    tid = st.theme_override or "rainbow"
    for i, sid in enumerate(ids):
        r, c = i % rows, i // rows
        y, x = 3 + r, 2 + c * cell
        if y >= h - 1:
            continue
        ph = (i / float(len(ids)) + now / 5.0) % 1.0
        _put(scr, y, x, rpad(frame_at(sid, now, phase=i * 0.41), GLYPH_W),
             at.color(theme_rgb(tid, "frame", ph), True), maxw=GLYPH_W)
        _put(scr, y, x + GLYPH_W + 1, SPINNERS[sid]["name"], at.DIM, maxw=label_w)


def draw_help(scr, at, h, w):
    _put(scr, 1, 2, "keys", at.BOLD)
    for i, (k, d) in enumerate(HELP_LINES):
        if 3 + i >= h - 1:
            break
        _put(scr, 3 + i, 4, pad(k, 12), at.BOLD)
        _put(scr, 3 + i, 17, d, at.DIM)
    _put(scr, min(h - 2, 4 + len(HELP_LINES)), 4, "any key to go back", at.DIM)


def draw_gallery(scr, at, st, now, searching=False, buf=""):
    """Render one whole frame. The only function that knows the layout."""
    h, w = scr.getmaxyx()
    scr.erase()

    if st.mode == "help":
        draw_help(scr, at, h, w)
        return
    if st.mode == "wall":
        draw_wall(scr, at, st, now, h, w)
        return

    rows = st.filtered()
    title = "obscene-spinner"
    _put(scr, 0, 2, title, at.BOLD)
    bits = [plural(len(rows), "pack")]
    if st.category:
        bits.append("in " + st.category)
    if st.sfw:
        bits.append("sfw only")
    if st.spinner_override:
        bits.append(get_spinner(st.spinner_override)["name"])
    if st.theme_override:
        bits.append(get_theme(st.theme_override)["name"])
    _put(scr, 0, 4 + disp_width(title), " · ".join(bits), at.DIM)
    # The key hints are the discoverability of the whole thing, so drop them
    # progressively rather than letting the line get chopped mid-word.
    hints = ["↑↓ move", "enter apply", "/ search", "tab category", "s sfw",
             "f anim", "t theme", "w wall", "r random", "u restore", "? help",
             "q quit"]
    while len(hints) > 2 and disp_width(" · ".join(hints)) > w - 4:
        hints.pop(-2)
    _put(scr, 1, 2, " · ".join(hints), at.DIM)

    split = max(24, min(int(w * 0.42), w - 34)) if w >= 62 else w
    listw = split - 3
    body_h = h - 5

    top = 0
    if rows and body_h > 0:
        st.idx = max(0, min(st.idx, len(rows) - 1))
        if st.idx >= body_h:
            top = st.idx - body_h + 1
    for i, pack in enumerate(rows[top:top + max(0, body_h)]):
        draw_row(scr, at, 3 + i, 1, st, pack, now, top + i == st.idx, listw)
    if not rows:
        _put(scr, 3, 2, "nothing matches %r — esc to clear" % st.query, at.DIM)

    if w >= 62:
        for y in range(3, h - 1):
            _put(scr, y, split - 1, "│", at.DIM)
        draw_detail(scr, at, 3, split + 1, w - split - 2, h - 4,
                    st, st.current(), now)

    _put(scr, h - 1, 2, ("/" + buf) if searching else st.status,
         at.BOLD if searching else at.DIM)


def render_gallery_text(width=100, height=30, now=0.0, sfw=False, spinner=None,
                        theme=None, query="", category=None, mode="list",
                        idx=0, packs=None):
    """Draw one gallery frame into a string. No terminal required."""
    st = GalleryState(packs=packs, sfw=sfw, spinner=spinner, theme=theme)
    st.set_query(query)
    st.category = category
    st.mode = mode
    st.idx = idx
    scr = TextScreen(height, width)
    draw_gallery(scr, Attrs(), st, now)
    return scr.text()


def gallery(url=None, sfw=False, spinner=None, theme=None, screenshot=None,
            keys=""):
    """The browsable, live-animating pack gallery.

    Returns a selection dict ({"pack", "spinner", "theme"}), the string
    "restore", or None. `screenshot` writes one rendered frame to that path and
    exits, after replaying `keys` as if they had been typed.
    """
    import curses

    st = GalleryState(sfw=sfw, spinner=spinner, theme=theme)
    result = {"value": None}
    pending = list(keys or "")

    def run(stdscr):
        curses.curs_set(0)
        stdscr.keypad(True)
        stdscr.timeout(int(TICK * 1000))
        at = CursesAttrs(curses)
        start = time.monotonic()
        searching = False
        buf = ""

        while True:
            now = time.monotonic() - start
            draw_gallery(stdscr, at, st, now, searching, buf)
            stdscr.refresh()

            if screenshot is not None and not pending:
                h, _ = stdscr.getmaxyx()
                # Re-render through the text backend: curses' instr() silently
                # drops non-ASCII cells, which is most of what we want to see.
                shot = TextScreen(*stdscr.getmaxyx())
                draw_gallery(shot, Attrs(), st, now, searching, buf)
                with open(screenshot, "w", encoding="utf-8") as f:
                    f.write(shot.text() + "\n")
                return

            if pending:
                k = ord(pending.pop(0))
            else:
                try:
                    k = stdscr.getch()
                except KeyboardInterrupt:
                    return
            if k == -1 or k == curses.KEY_RESIZE:
                continue

            if searching:
                if k in (10, 13, curses.KEY_ENTER):
                    searching = False
                elif k == 27:                      # esc clears the search
                    searching, buf = False, ""
                    st.set_query("")
                elif k in (curses.KEY_BACKSPACE, 127, 8):
                    buf = buf[:-1]
                    st.set_query(buf)
                elif 32 <= k < 127:
                    buf += chr(k)
                    st.set_query(buf)
                continue

            if st.mode in ("help", "wall"):
                if k in (ord("q"), 27, ord("w"), ord("?"), 10, 13):
                    st.mode = "list"
                continue

            if k in (curses.KEY_UP, ord("k")):
                st.move(-1)
            elif k in (curses.KEY_DOWN, ord("j")):
                st.move(1)
            elif k == curses.KEY_PPAGE:
                st.move(-10)
            elif k == curses.KEY_NPAGE:
                st.move(10)
            elif k in (curses.KEY_HOME, ord("g")):
                st.idx = 0
            elif k in (curses.KEY_END, ord("G")):
                st.idx = max(0, len(st.filtered()) - 1)
            elif k == ord("/"):
                searching, buf = True, ""
                st.set_query("")
            elif k == 9:                           # tab
                st.cycle_category(1)
            elif k == curses.KEY_BTAB:
                st.cycle_category(-1)
            elif k == ord("s"):
                st.toggle_sfw()
            elif k == ord("f"):
                st.cycle_spinner(1)
            elif k == ord("F"):
                st.cycle_spinner(-1)
            elif k == ord("t"):
                st.cycle_theme(1)
            elif k == ord("T"):
                st.cycle_theme(-1)
            elif k == ord("r"):
                st.randomise()
            elif k == ord("w"):
                st.mode = "wall"
            elif k == ord("?"):
                st.mode = "help"
            elif k == ord("u"):
                result["value"] = "restore"
                return
            elif k in (10, 13, curses.KEY_ENTER):
                result["value"] = st.selection()
                return
            elif k in (ord("q"), 27):
                return

    try:
        curses.wrapper(run)
    except Exception as e:
        _write("the gallery needs a terminal curses can drive (%s)\n"
               "try --list, or --pack ID to preview one.\n" % e)
        return None
    return result["value"]


# ---------------------------------------------------------------------------
# Applying, backing up and restoring
# ---------------------------------------------------------------------------

def _atomic_write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)  # atomic: a reader sees old or new, never half


def _load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def backup_once(settings_path=SETTINGS, backup_path=BACKUP_FILE,
                theme_path=THEME_FILE):
    """Stash whatever we're about to overwrite, BEFORE we ever touch it.

    Written exactly once: the second apply must not overwrite the backup with
    our own pack, or --restore would hand back a pack we installed instead of
    the thing they started with. Records explicit sentinels where there was
    nothing at all, so restore knows to delete rather than guess.

    Covers two things: the user's spinnerVerbs, and any theme file already
    sitting at our slug (someone may have hand-written one before installing).
    """
    if os.path.exists(backup_path):
        return False
    data = _load_json(settings_path, {}) or {}
    prior = data.get("spinnerVerbs")
    prior_theme = _load_json(theme_path)
    _atomic_write_json(backup_path, {
        "absent": prior is None,
        "spinnerVerbs": prior,
        "claudeThemeAbsent": prior_theme is None,
        "claudeTheme": prior_theme,
    })
    return True


def apply_pack(verbs, mode, settings_path=SETTINGS, mode_path=MODE_FILE,
               backup_path=BACKUP_FILE, state=None, state_path=STATE_FILE,
               theme_path=THEME_FILE):
    """Write the pack into Claude Code's spinnerVerbs (mode 'replace'), atomically,
    preserving every other setting. Records the mode so the poller knows whether
    to keep refreshing headlines, and backs up the previous value once so
    --restore can put it back."""
    backup_once(settings_path, backup_path, theme_path)
    data = _load_json(settings_path, {})
    if not isinstance(data, dict):
        data = {}
    data["spinnerVerbs"] = {"mode": "replace", "verbs": list(verbs)}
    _atomic_write_json(settings_path, data)
    os.makedirs(os.path.dirname(mode_path) or ".", exist_ok=True)
    with open(mode_path, "w", encoding="utf-8") as f:
        f.write(mode)  # legacy marker: the news poller reads this
    if state is not None:
        prev = _load_json(state_path, {}) or {}
        payload = dict(state)
        payload["applied_at"] = int(time.time())
        if prev.get("pack") and prev.get("pack") != payload.get("pack"):
            payload["previous"] = prev.get("pack")
        else:
            payload["previous"] = prev.get("previous")
        _atomic_write_json(state_path, payload)


def restore_pack(settings_path=SETTINGS, backup_path=BACKUP_FILE,
                 mode_path=MODE_FILE, state_path=STATE_FILE,
                 theme_path=THEME_FILE):
    """Put back everything the user had before the first apply — spinnerVerbs
    and the theme file. Returns a human-readable outcome string."""
    backup = _load_json(backup_path)
    if backup is None:
        return "nothing to restore — no backup was taken."
    data = _load_json(settings_path, {})
    if not isinstance(data, dict):
        data = {}
    if backup.get("absent"):
        data.pop("spinnerVerbs", None)
        what = ["Claude Code's own verbs"]
    else:
        data["spinnerVerbs"] = backup.get("spinnerVerbs")
        what = ["your previous spinnerVerbs"]
    _atomic_write_json(settings_path, data)

    # The colour half: either hand back the theme file that was there before us,
    # or take ours away entirely.
    had_theme = os.path.exists(theme_path)
    if backup.get("claudeThemeAbsent", True):
        if remove_claude_theme(theme_path):
            what.append("removed the spinner colour")
    else:
        _atomic_write_json(theme_path, backup.get("claudeTheme"))
        what.append("your previous %s.json" % THEME_SLUG)

    for path in (backup_path, state_path, mode_path):
        try:
            os.remove(path)
        except OSError:
            pass
    msg = "✓ restored %s." % " and ".join(what)
    if had_theme:
        msg += ("\n  If you picked \"%s\" in /theme, switch back to your own theme "
                "there too." % THEME_SLUG)
    return msg


def read_state(state_path=STATE_FILE, mode_path=MODE_FILE):
    """Current selection. Prefers the JSON state, falls back to the legacy
    plaintext mode file so an install that predates this version still reports
    something sensible."""
    st = _load_json(state_path)
    if isinstance(st, dict) and st.get("pack"):
        return st
    try:
        with open(mode_path, encoding="utf-8") as f:
            legacy = f.read().strip()
    except OSError:
        legacy = ""
    if legacy == "news":
        return {"pack": "news", "spinner": "earth", "theme": "ice"}
    if legacy:
        return {"pack": "profanity", "spinner": "braille", "theme": "acid"}
    return {}


def current_mode():
    """Legacy accessor: "verbs" or "news"."""
    st = read_state()
    return "news" if st.get("pack") == "news" else "verbs"


# Old --set values map onto pack ids; keep the words working forever.
LEGACY_ALIASES = {"verbs": "profanity", "profanity": "profanity", "news": "news"}


def _theme_notes(tid, base, existed, payload):
    """What to tell the user after writing the theme file.

    First time round they have to select it in /theme once — nothing in the
    documented settings surface lets a tool change the theme preference, and
    guessing at an undocumented one would be worse than asking. After that the
    same file is rewritten on every apply and Claude Code hot-reloads it, so
    the colour just follows the pack.
    """
    out = []
    swatch = payload["overrides"]["claude"]
    if existed:
        out.append("  Spinner colour → %s (%s). Applies live." % (swatch, tid))
    else:
        out.append("  Spinner colour → %s (%s), written to ~/.claude/themes/%s.json."
                   % (swatch, tid, THEME_SLUG))
        out.append("  One-off: run /theme in Claude Code and pick \"%s\" to turn it on."
                   % THEME_SLUG)
        out.append("  (If ~/.claude/themes/ didn't exist yet, restart Claude Code once.)")
        out.append("  It starts from the \"%s\" preset — that sets the rest of the UI "
                   "too; --theme-base changes it." % base)
    return out


def apply_selected(mode, url=None, spinner=None, theme=None, quiet=False,
                   theme_base=DEFAULT_THEME_BASE, write_theme=True):
    """Apply a pack by id (or a legacy mode word). Returns True on success."""
    pid = LEGACY_ALIASES.get(mode, mode)
    if pid == "toggle":
        st = read_state()
        pid = st.get("previous") or ("profanity" if st.get("pack") == "news"
                                     else "news")
    pack = get_pack(pid)
    if pack is None:
        print("no pack %r — try --list." % mode)
        return False
    verbs = resolve_verbs(pack, url)
    if not verbs:
        if pack.get("kind") == "live":
            print("no feed at %s — nothing applied." % (url or NEWS_URL))
        else:
            print("pack %r has no verbs — nothing applied." % pack["id"])
        return False
    over = [v for v in verbs if disp_width(v) > SPINNER_MAX]
    verbs = [condense(v, SPINNER_MAX) if disp_width(v) > SPINNER_MAX else v
             for v in verbs]
    sid = spinner or pack.get("spinner", DEFAULT_SPINNER)
    tid = theme or pack.get("theme", DEFAULT_THEME)
    legacy = "news" if pack.get("kind") == "live" else "verbs"
    apply_pack(verbs, legacy, state={"pack": pack["id"], "spinner": sid,
                                     "theme": tid})
    notes = []
    if write_theme:
        payload, existed = write_claude_theme(tid, theme_base)
        notes = _theme_notes(tid, theme_base, existed, payload)
    if not quiet:
        print("✓ %s is now your spinner (%s)." % (pack["name"], plural(len(verbs), "verb")))
        if over:
            print("  %d were wider than %d columns and were condensed."
                  % (len(over), SPINNER_MAX))
        if pack.get("kind") == "live":
            print("  The poller keeps it fresh; open a new session for the "
                  "latest wire.")
        else:
            print("  Shows next time Claude Code spins one up.")
        for line in notes:
            print(line)
        print("  `%s --restore` puts your old spinner back."
              % os.path.basename(sys.argv[0] or "spin.py"))
    return True


def apply_mix(pack_ids, url=None, spinner=None, theme=None,
              theme_base=DEFAULT_THEME_BASE, write_theme=True):
    """Apply several packs blended into one spinner list."""
    packs = []
    for pid in pack_ids:
        p = get_pack(LEGACY_ALIASES.get(pid, pid))
        if p is None:
            print("no pack %r — try --list." % pid)
            return False
        packs.append(p)
    verbs, seen = [], set()
    for p in packs:
        for v in resolve_verbs(p, url):
            if v not in seen:
                seen.add(v)
                verbs.append(v)
    if not verbs:
        print("nothing to mix.")
        return False
    random.shuffle(verbs)
    base = packs[0]
    sid = spinner or base.get("spinner", DEFAULT_SPINNER)
    tid = theme or base.get("theme", DEFAULT_THEME)
    apply_pack(verbs, "verbs", state={"pack": "+".join(p["id"] for p in packs),
                                      "spinner": sid, "theme": tid})
    print("✓ mixed %s — %s."
          % (", ".join(p["name"] for p in packs), plural(len(verbs), "verb")))
    if write_theme:
        payload, existed = write_claude_theme(tid, theme_base)
        for line in _theme_notes(tid, theme_base, existed, payload):
            print(line)
    return True


# ---------------------------------------------------------------------------
# Listing, export, import
# ---------------------------------------------------------------------------

def packs_payload(packs=None, sfw=False, query=""):
    packs = all_packs() if packs is None else packs
    st = GalleryState(packs=packs, sfw=sfw)
    st.set_query(query)
    out = []
    for p in st.filtered():
        entry = {"id": p["id"], "name": p.get("name"), "desc": p.get("desc"),
                 "category": p.get("category"), "rating": p.get("rating"),
                 "spinner": p.get("spinner"), "theme": p.get("theme"),
                 "kind": p.get("kind", "static"), "custom": bool(p.get("custom"))}
        entry["count"] = len(p.get("verbs", [])) or None
        out.append(entry)
    return {
        "packs": out,
        "spinners": [{"id": k, "name": v["name"], "frames": len(v["frames"]),
                      "interval": v["interval"], "width": frame_width(k)}
                     for k, v in SPINNERS.items()],
        "themes": [{"id": k, "name": v["name"], "style": v.get("style", "solid")}
                   for k, v in THEMES.items()],
    }


def print_list(sfw=False, query="", as_json=False, verbose=False, mode=None):
    payload = packs_payload(sfw=sfw, query=query)
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    mode = color_mode() if mode is None else mode
    idw = max([len(p["id"]) for p in payload["packs"]] + [4])
    print("packs (%d)" % len(payload["packs"]))
    for p in payload["packs"]:
        badge = {"sfw": "  ", "mild": " ~", "nsfw": " !"}[p["rating"]]
        count = "%3d" % p["count"] if p["count"] else "  ·"
        print("  %s %s %s  %-12s %s"
              % (badge, p["id"].ljust(idw), count, p["category"], p["desc"]))
        if verbose:
            pack = get_pack(p["id"])
            for line in preview_block(pack, mode=mode, n=3):
                print("      " + line)
    print("\nanimations (%d)" % len(payload["spinners"]))
    row = []
    for s in payload["spinners"]:
        frames = get_spinner(s["id"])["frames"]
        # Space-separate multi-character frames or "▰▱▱▱▱▰▰▱▱▱" reads as noise,
        # and pad by display columns — ljust counts codepoints, so a row of
        # emoji frames would drift a column per glyph.
        wide = max(len(f) for f in frames) > 1
        sep, take = (" ", 2) if wide else ("", 6)
        row.append(pad(s["id"], 12) + " " + pad(sep.join(frames[:take]), 14))
        if len(row) == 3:
            print("  " + "  ".join(row).rstrip())
            row = []
    if row:
        print("  " + "  ".join(row).rstrip())
    print("\nthemes (%d)" % len(payload["themes"]))
    row = []
    for t in payload["themes"]:
        swatch = t["id"]
        if mode != "none":
            swatch = (sgr(theme_rgb(t["id"], "frame", 0.25), mode, True)
                      + "●●●" + RESET + " " + t["id"])
        row.append("%-22s" % swatch)
        if len(row) == 4:
            print("  " + " ".join(row))
            row = []
    if row:
        print("  " + " ".join(row))
    errs = user_pack_errors()
    if errs:
        print("\ncustom pack problems:")
        for e in errs:
            print("  ! " + e)


def export_pack(pid, out_path=None, url=None):
    pack = get_pack(pid)
    if pack is None:
        print("no pack %r — try --list." % pid)
        return False
    payload = {"id": pack["id"], "name": pack.get("name"),
               "desc": pack.get("desc"), "category": pack.get("category"),
               "rating": pack.get("rating"), "spinner": pack.get("spinner"),
               "theme": pack.get("theme"), "verbs": resolve_verbs(pack, url)}
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print("✓ wrote %s" % out_path)
    else:
        print(text)
    return True


def import_pack(path, dest_dir=None):
    dest_dir = USER_PACK_DIR if dest_dir is None else dest_dir
    raw = _load_json(path)
    if raw is None:
        print("could not read JSON from %s" % path)
        return False
    ok = 0
    for item in (raw if isinstance(raw, list) else [raw]):
        pack, errs = validate_pack(item, os.path.basename(path))
        for e in errs:
            print("  ! " + e)
        if not pack:
            continue
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, pack["id"] + ".json")
        _atomic_write_json(dest, {k: v for k, v in pack.items() if k != "custom"})
        print("✓ installed %s (%s) -> %s"
              % (pack["id"], plural(len(pack["verbs"]), "verb"), dest))
        ok += 1
    all_packs(refresh=True)
    return ok > 0


def _frame_sample(sid, take=8):
    """A readable sample of an animation's frames. Multi-character frames get
    spaces between them or "▰▱▱▱▱▰▰▱▱▱" reads as one long smear."""
    frames = get_spinner(sid)["frames"]
    wide = max(len(f) for f in frames) > 1
    return (" " if wide else "").join(frames[:4 if wide else take])


def _code(text):
    """A markdown code span that survives a backtick in its content (the flip
    animation contains one), per CommonMark's doubled-delimiter rule."""
    if "`" in text:
        return "`` %s ``" % text
    return "`%s`" % text


def docs_tables():
    """Markdown tables for the README, generated from the registry so the docs
    cannot drift from the code."""
    out = ["| pack | verbs | rating | what it is |", "| --- | --- | --- | --- |"]
    for p in all_packs(include_user=False):
        n = len(p.get("verbs", [])) or "live"
        out.append("| `%s` | %s | %s | %s |"
                   % (p["id"], n, p["rating"], p["desc"]))
    out += ["", "| animation | frames | animation | frames |",
            "| --- | --- | --- | --- |"]
    ids = list(SPINNERS)
    for i in range(0, len(ids), 2):
        cells = []
        for sid in ids[i:i + 2]:
            cells.append("`%s`" % sid)
            cells.append(_code(_frame_sample(sid)))
        while len(cells) < 4:
            cells.append("")
        out.append("| " + " | ".join(cells) + " |")
    # The `claude` column is the colour Claude Code's own spinner actually
    # becomes, so the table doubles as the reference for what you're picking.
    out += ["", "| theme | style | spinner colour | theme | style | spinner colour |",
            "| --- | --- | --- | --- | --- | --- |"]
    tids = list(THEMES)
    for i in range(0, len(tids), 2):
        cells = []
        for tid in tids[i:i + 2]:
            cells.append("`%s`" % tid)
            cells.append(THEMES[tid].get("style", "solid"))
            cells.append("`%s`" % hex_of(THEMES[tid]["frame"]))
        while len(cells) < 6:
            cells.append("")
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def demo():
    """Run the test suite. Prefers tests/ next to this file; falls back to a
    smoke check so a lone copied spin.py still self-verifies."""
    import unittest
    here = os.path.dirname(os.path.abspath(__file__))
    tests = os.path.join(here, "tests")
    if os.path.isdir(tests):
        # The suite contains a test that runs `spin.py --selftest` in a
        # subprocess. Without this marker that test would re-enter the suite,
        # which would run it again, forever.
        os.environ["SPIN_IN_SELFTEST"] = "1"
        loader = unittest.TestLoader()
        suite = loader.discover(tests, top_level_dir=here)
        runner = unittest.TextTestRunner(verbosity=1)
        result = runner.run(suite)
        if not result.wasSuccessful():
            raise SystemExit(1)
        print("ok")
        return
    # Embedded fallback: the invariants that matter most, without the tests dir.
    g = shuffle_bag(["a", "b", "c"])
    seq = [next(g) for _ in range(60)]
    assert all(x != y for x, y in zip(seq, seq[1:])), "repeated back-to-back"
    for sid, sp in SPINNERS.items():
        widths = set(disp_width(f) for f in sp["frames"])
        assert len(widths) == 1, (sid, widths)
    ids = [p["id"] for p in PACKS]
    assert len(ids) == len(set(ids)), "duplicate pack ids"
    for p in PACKS:
        for v in p.get("verbs", []):
            assert 0 < disp_width(v) <= SPINNER_MAX, (p["id"], v)
    assert fit("short", 80) == "short"
    assert condense("Stocks rise on tech rally — Bloomberg", 56) == \
        "Stocks rise on tech rally"
    print("ok (embedded smoke test; tests/ not found)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="spin",
        description="Obscene Spinner — spinner packs for Claude Code.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="run bare in a terminal for the gallery: browse every pack, "
               "animation and theme, press enter to apply.")
    p.add_argument("--pack", metavar="ID",
                   help="preview a pack (id, or a unique prefix)")
    p.add_argument("--spinner", metavar="NAME", help="animation to use")
    p.add_argument("--theme", metavar="NAME", help="colour theme to use")
    p.add_argument("--interval", type=float, default=None,
                   help="seconds per item (default 0.6 verbs, 5 news)")
    p.add_argument("--random", action="store_true",
                   help="preview a random pack/animation/theme")
    p.add_argument("--wall", action="store_true",
                   help="every animation running at once")
    p.add_argument("--once", action="store_true", help="print one verb and exit")
    p.add_argument("--list", action="store_true",
                   help="list every pack, animation and theme")
    p.add_argument("--json", action="store_true", help="machine-readable --list")
    p.add_argument("--verbose", action="store_true",
                   help="with --list, show a sample of each pack")
    p.add_argument("--search", metavar="TEXT", default="",
                   help="filter packs by name, description or verb")
    p.add_argument("--sfw", action="store_true", help="safe-for-work packs only")
    p.add_argument("--color", dest="color", metavar="MODE", default=None,
                   choices=("truecolor", "256", "16", "none"),
                   help="force a colour mode")
    p.add_argument("--no-color", dest="color", action="store_const", const="none",
                   help="plain text, no escape codes")
    p.add_argument("--set", metavar="ID",
                   help="apply a pack to your spinner without the gallery "
                        "(also accepts verbs|news|toggle)")
    p.add_argument("--mix", nargs="+", metavar="ID",
                   help="apply several packs blended together")
    p.add_argument("--theme-base", choices=THEME_BASES,
                   default=DEFAULT_THEME_BASE,
                   help="preset the written Claude Code theme starts from "
                        "(default %s; it sets the rest of the UI too)"
                        % DEFAULT_THEME_BASE)
    p.add_argument("--no-theme", action="store_true",
                   help="apply verbs only — don't recolour Claude Code's spinner")
    p.add_argument("--print-theme", metavar="ID", nargs="?", const="",
                   help="print the Claude Code theme JSON for a theme and exit")
    p.add_argument("--restore", action="store_true",
                   help="put back the spinnerVerbs and colour you had before")
    p.add_argument("--status", action="store_true",
                   help="print the current spinner selection and exit")
    p.add_argument("--export", metavar="ID", help="print a pack as JSON")
    p.add_argument("--output", "-o", metavar="FILE", help="write --export here")
    p.add_argument("--import", dest="import_path", metavar="FILE",
                   help="install a pack JSON into ~/.claude/spinner-packs")
    p.add_argument("--news-url", default=NEWS_URL,
                   help="headline feed URL (or set SPIN_NEWS_URL)")
    p.add_argument("--verbs", action="store_true",
                   help="preview the original profanity pack")
    p.add_argument("--news", action="store_true",
                   help="preview live headlines (press n to read a summary)")
    p.add_argument("--docs-table", action="store_true",
                   help=argparse.SUPPRESS)  # README tables, generated
    p.add_argument("--screenshot", metavar="FILE",
                   help=argparse.SUPPRESS)  # dump one gallery frame (tests)
    p.add_argument("--screenshot-keys", default="",
                   help=argparse.SUPPRESS)  # keys to replay before the dump
    p.add_argument("--selftest", action="store_true", help="run internal checks")
    return p


def main(argv=None):
    # Behave like a normal Unix filter when the reader goes away (`| head`).
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass  # no SIGPIPE on this platform; _write() handles BrokenPipeError

    a = build_parser().parse_args(argv)
    mode = color_mode(force=a.color)

    if a.selftest:
        demo()
        return
    if a.docs_table:
        print(docs_tables())
        return
    if a.screenshot:
        gallery(a.news_url, sfw=a.sfw, spinner=a.spinner, theme=a.theme,
                screenshot=a.screenshot, keys=a.screenshot_keys)
        return
    if a.list:
        print_list(sfw=a.sfw, query=a.search, as_json=a.json,
                   verbose=a.verbose, mode=mode)
        return
    if a.export:
        raise SystemExit(0 if export_pack(a.export, a.output, a.news_url) else 1)
    if a.import_path:
        raise SystemExit(0 if import_pack(a.import_path) else 1)
    if a.restore:
        print(restore_pack())
        return
    if a.print_theme is not None:
        tid = a.print_theme or a.theme or DEFAULT_THEME
        if tid not in THEMES:
            print("no theme %r — try --list." % tid)
            raise SystemExit(1)
        print(json.dumps(claude_theme_payload(tid, a.theme_base), indent=2))
        return
    if a.status:
        st = read_state()
        if not st:
            print("spinner: unset (Claude Code's own verbs)")
        else:
            print("spinner: %s · animation %s · theme %s"
                  % (st.get("pack"), st.get("spinner", DEFAULT_SPINNER),
                     st.get("theme", DEFAULT_THEME)))
        # Always report the colour: the theme file is independent of the pack,
        # so --no-theme or a leftover file both need to be visible here.
        live = _load_json(THEME_FILE)
        if live:
            print("colour:  %s from ~/.claude/themes/%s.json (base %s)"
                  % (live.get("overrides", {}).get("claude", "?"),
                     THEME_SLUG, live.get("base", "?")))
            print("         active only if /theme has \"%s\" selected."
                  % THEME_SLUG)
        else:
            print("colour:  not set — Claude Code's own spinner colour")
        return
    if a.mix:
        raise SystemExit(0 if apply_mix(a.mix, a.news_url, a.spinner, a.theme,
                                        a.theme_base, not a.no_theme) else 1)
    if a.set:
        raise SystemExit(0 if apply_selected(a.set, a.news_url, a.spinner,
                                             a.theme, theme_base=a.theme_base,
                                             write_theme=not a.no_theme) else 1)

    for e in user_pack_errors():
        print("! custom pack: " + e, file=sys.stderr)

    # Pick what to preview.
    pack = None
    spinner, theme = a.spinner, a.theme
    if a.random:
        choices = [p for p in all_packs()
                   if p.get("kind") != "live" and (not a.sfw or p["rating"] == "sfw")]
        pack = random.choice(choices)
        spinner = spinner or random.choice(list(SPINNERS))
        theme = theme or random.choice(list(THEMES))
    elif a.pack:
        pack = get_pack(a.pack)
        if pack is None:
            print("no pack %r — try --list." % a.pack)
            raise SystemExit(1)
    elif a.verbs:
        pack = get_pack("profanity")

    if a.once:
        target = pack or get_pack("profanity")
        verbs = resolve_verbs(target, a.news_url)
        if not verbs:
            raise SystemExit(1)
        print(random.choice(verbs))
        return
    if a.wall:
        wall(theme=theme, mode=mode)
        return
    if a.news or (pack is not None and pack.get("kind") == "live"):
        spin_news(a.interval if a.interval is not None else 5.0, a.news_url,
                  spinner or "earth", theme or "ice", mode)
        return
    if pack is not None:
        spin(a.interval if a.interval is not None else 0.6, pack, spinner,
             theme, mode, a.news_url)
        return

    # Bare run = the gallery, which APPLIES your choice to Claude Code's real
    # spinner (the whole point — it spins while your prompts are processed).
    if sys.stdin.isatty() and sys.stdout.isatty():
        choice = gallery(a.news_url, sfw=a.sfw, spinner=a.spinner, theme=a.theme)
        if choice == "restore":
            print(restore_pack())
        elif choice:
            apply_selected(choice["pack"], a.news_url, choice["spinner"],
                           choice["theme"], theme_base=a.theme_base,
                           write_theme=not a.no_theme)
    else:
        spin(a.interval if a.interval is not None else 0.6,
             get_pack("profanity"), spinner, theme, mode)  # piped: preview


if __name__ == "__main__":
    main()
