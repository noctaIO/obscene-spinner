#!/usr/bin/env python3
"""Generate the README artwork: demo.svg, still.svg and gallery.svg.

Self-contained SVG with SMIL animation, which GitHub renders as an image and
animates in the README. No browser, no recorder, no dependencies.

Everything is driven from spin.py's registries, so the art can never show a
pack, animation or colour the program doesn't actually have. CI regenerates
these and fails on a diff.

    python3 make_demo.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spin  # noqa: E402

FONT = "SFMono-Regular,Consolas,'Liberation Mono',Menlo,monospace"
BG, BAR, TXT, DIM = "#0d1117", "#161b22", "#e6edf3", "#7d8590"
CH = 0.60          # monospace advance width, in ems — used to place columns


def hexof(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def stack(items, x, y, size, dur, fill, anchor="start", weight=None):
    """SMIL can't animate text content, so stack the frames on top of each
    other and pulse their opacity — only one is visible at a time."""
    n = len(items)
    out = []
    for i, item in enumerate(items):
        a, b = i / n, (i + 1) / n
        kt = "0;%.4f;%.4f;%.4f;%.4f;1" % (a, a, b, b)
        w = ' font-weight="%s"' % weight if weight else ""
        out.append(
            '  <text x="%.1f" y="%.1f" fill="%s" font-size="%d" '
            'text-anchor="%s"%s opacity="0">%s'
            '<animate attributeName="opacity" values="0;0;1;1;0;0" '
            'keyTimes="%s" dur="%.2fs" repeatCount="indefinite" '
            'calcMode="discrete"/></text>' % (x, y, fill, size, anchor, w,
                                              esc(str(item)), kt, dur))
    return "\n".join(out)


def card(width, height, title, inner):
    dots = "".join(
        '<circle cx="%d" cy="22" r="6" fill="%s"/>' % (28 + i * 22, c)
        for i, c in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d" font-family="%s">\n'
            '  <rect width="%d" height="%d" rx="12" fill="%s"/>\n'
            '  <rect width="%d" height="44" rx="12" fill="%s"/>\n'
            '  <rect y="32" width="%d" height="12" fill="%s"/>\n'
            '  %s\n'
            '  <text x="%.1f" y="27" fill="%s" font-size="14" '
            'text-anchor="middle">%s</text>\n%s\n</svg>\n'
            % (width, height, width, height, FONT, width, height, BG, width,
               BAR, width, BAR, dots, width / 2.0, DIM, esc(title), inner))


# --------------------------------------------------------------------------
# demo.svg — one pack, running fast, the way the original looked
# --------------------------------------------------------------------------

DEMO_VERBS = [
    "faffing", "unfucking", "yak-shaving", "bikeshedding", "doomscrolling",
    "footgunning", "yeeting-to-prod", "polishing-a-turd", "malding",
    "kludging", "rawdogging", "crashing-out",
]
DT = 0.7
W, H = 860, 230
X, Y = 44, 138


def build_demo():
    pack = spin.get_pack("profanity")
    verbs = [v for v in DEMO_VERBS if v in pack["verbs"]]
    assert len(verbs) == len(DEMO_VERBS), "demo verbs drifted from the pack"
    sp = spin.get_spinner(pack["spinner"])
    green = hexof(spin.theme_rgb(pack["theme"], "frame", 0.0))
    parts = [
        stack(sp["frames"], X, Y, 30, len(sp["frames"]) * 0.09, green),
        stack([v + "…" for v in verbs], X + 34, Y, 30, len(verbs) * DT, TXT),
        '  <text x="%d" y="%d" fill="%s" font-size="16">'
        '· 2.1s · 14.2K tok · %d packs · %d animations</text>'
        % (X, Y + 40, DIM, len(spin.PACKS), len(spin.SPINNERS)),
    ]
    return card(W, H, "claude — obscene-spinner", "\n".join(parts))


def build_still():
    pack = spin.get_pack("profanity")
    green = hexof(spin.theme_rgb(pack["theme"], "frame", 0.0))
    parts = [
        '  <text x="%d" y="%d" fill="%s" font-size="30">%s</text>'
        % (X, Y, green, spin.get_spinner(pack["spinner"])["frames"][3]),
        '  <text x="%d" y="%d" fill="%s" font-size="30">polishing-a-turd…</text>'
        % (X + 34, Y, TXT),
        '  <text x="%d" y="%d" fill="%s" font-size="16">'
        '· 2.1s · 14.2K tok · godel-news AAPL</text>' % (X, Y + 40, DIM),
    ]
    return card(W, H, "claude — obscene-spinner", "\n".join(parts))


# --------------------------------------------------------------------------
# gallery.svg — the point of the whole thing: many packs at once
# --------------------------------------------------------------------------

SHOWCASE = ["profanity", "british", "eldritch", "pirate", "corporate",
            "sysadmin", "wizard", "genz", "noir", "zen"]
GW, ROW_H, TOP = 900, 34, 78
SIZE = 19
NAME_X, GLYPH_X, VERB_X = 34, 190, 230


def build_gallery():
    packs = [spin.get_pack(p) for p in SHOWCASE]
    height = TOP + ROW_H * len(packs) + 34
    parts = []
    for i, pack in enumerate(packs):
        y = TOP + i * ROW_H
        sp = spin.get_spinner(pack["spinner"])
        # Sample the theme across its cycle so gradient themes visibly breathe.
        colours = [hexof(spin.theme_rgb(pack["theme"], "frame", k / 12.0))
                   for k in range(12)]
        verbs = spin.stable_order(pack["id"], pack["verbs"])[:7]

        parts.append('  <text x="%d" y="%.1f" fill="%s" font-size="%d">%s</text>'
                     % (NAME_X, y, DIM, SIZE, esc(pack["name"])))
        # The glyph animates on the animation's own interval; the colour on a
        # slower one, so the two never lock into a single visible period.
        parts.append(stack(sp["frames"], GLYPH_X, y, SIZE + 3,
                           len(sp["frames"]) * sp["interval"], colours[0]))
        parts.append(
            '  <rect x="%d" y="%.1f" width="14" height="2" fill="%s" '
            'opacity="0.55"><animate attributeName="fill" values="%s" '
            'dur="4s" repeatCount="indefinite"/></rect>'
            % (NAME_X - 22, y - 6, colours[0], ";".join(colours + [colours[0]])))
        parts.append(stack([v + "…" for v in verbs], VERB_X, y, SIZE, 7 * 1.05,
                           TXT))

    parts.append('  <text x="%d" y="%.1f" fill="%s" font-size="15">'
                 '%d packs · %d animations · %d themes · one Python file</text>'
                 % (NAME_X, TOP + ROW_H * len(packs) + 14, DIM,
                    len(spin.PACKS), len(spin.SPINNERS), len(spin.THEMES)))
    return card(GW, height, "claude — obscene-spinner · the gallery",
                "\n".join(parts))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    for name, svg in (("demo.svg", build_demo()),
                      ("still.svg", build_still()),
                      ("gallery.svg", build_gallery())):
        with open(os.path.join(here, name), "w", encoding="utf-8") as f:
            f.write(svg)
    print("wrote demo.svg, still.svg, gallery.svg")


if __name__ == "__main__":
    main()
