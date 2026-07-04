#!/usr/bin/env python3
"""Generate demo.svg (animated) and still.svg from the spinner look.

Self-contained SVG with SMIL animation, which GitHub renders as an image and
animates in the README. No browser, no recorder, no dependencies.
"""

FRAMES = list("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
VERBS = [
    "faffing", "unfucking", "yak-shaving", "bikeshedding", "doomscrolling",
    "footgunning", "yeeting-to-prod", "polishing-a-turd", "malding",
    "kludging", "rawdogging", "crashing-out",
]
DT = 0.7                      # seconds each verb is on screen
CYCLE = len(VERBS) * DT       # full loop length
FRAME_DUR = 0.09              # spinner glyph step

W, H = 860, 230
FONT = "SFMono-Regular,Consolas,'Liberation Mono',Menlo,monospace"
BG, BAR, TXT, DIM, GRN = "#0d1117", "#161b22", "#e6edf3", "#7d8590", "#3fb950"
X, Y = 44, 138               # baseline of the spinner line


def card(inner):
    dots = "".join(
        f'<circle cx="{28 + i*22}" cy="22" r="6" fill="{c}"/>'
        for i, c in enumerate(("#ff5f56", "#ffbd2e", "#27c93f"))
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}">
  <rect width="{W}" height="{H}" rx="12" fill="{BG}"/>
  <rect width="{W}" height="44" rx="12" fill="{BAR}"/>
  <rect y="32" width="{W}" height="12" fill="{BAR}"/>
  {dots}
  <text x="{W/2}" y="27" fill="{DIM}" font-size="14" text-anchor="middle">claude — obscene-spinner</text>
{inner}
</svg>'''


def spinner_glyph():
    # SMIL can't animate text content, so stack the frames and pulse opacity.
    n = len(FRAMES)
    spin_cycle = n * FRAME_DUR
    out = []
    for i, f in enumerate(FRAMES):
        a, b = i / n, (i + 1) / n
        kt = f"0;{a:.4f};{a:.4f};{b:.4f};{b:.4f};1"
        out.append(
            f'  <text x="{X}" y="{Y}" fill="{GRN}" font-size="30" opacity="0">{f}'
            f'<animate attributeName="opacity" values="0;0;1;1;0;0" keyTimes="{kt}" '
            f'dur="{spin_cycle:.2f}s" repeatCount="indefinite" calcMode="linear"/></text>')
    return "\n".join(out)


def verbs_stack():
    out = []
    for i, v in enumerate(VERBS):
        a, b = i * DT / CYCLE, (i + 1) * DT / CYCLE
        kt = f"0;{a:.4f};{a:.4f};{b:.4f};{b:.4f};1"
        out.append(
            f'  <text x="{X+34}" y="{Y}" fill="{TXT}" font-size="30" opacity="0">{v}…'
            f'<animate attributeName="opacity" values="0;0;1;1;0;0" keyTimes="{kt}" '
            f'dur="{CYCLE:.2f}s" repeatCount="indefinite" calcMode="linear"/></text>')
    return "\n".join(out)


def tail():
    return f'  <text x="{X}" y="{Y+40}" fill="{DIM}" font-size="16">· 2.1s · 14.2K tok · godel-news AAPL</text>'


demo = card("\n".join([spinner_glyph(), verbs_stack(), tail()]))
still = card("\n".join([
    f'  <text x="{X}" y="{Y}" fill="{GRN}" font-size="30">⠹</text>',
    f'  <text x="{X+34}" y="{Y}" fill="{TXT}" font-size="30">polishing-a-turd…</text>',
    tail(),
]))

open("demo.svg", "w").write(demo)
open("still.svg", "w").write(still)
print("wrote demo.svg, still.svg")
