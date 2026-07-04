#!/usr/bin/env python3
"""Obscene Spinner — the crude Claude Code spinner-verb pack, as a standalone toy.

Cycles a braille spinner through the verb pack. Default cadence is fast so you
actually see the verbs go by (the real Claude Code spinner only swaps a verb
when a new operation starts, so most never show). Ctrl-C to quit.

    ./spin.py                # ~0.6s per verb
    ./spin.py --interval 0.3 # faster
    ./spin.py --once         # print one random verb and exit (for scripts)
"""
import argparse
import itertools
import random
import sys
import time

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


def demo():
    # No immediate repeats, and every verb is reachable.
    g = verbs_no_repeat(VERBS)
    seq = [next(g) for _ in range(2000)]
    assert all(a != b for a, b in zip(seq, seq[1:])), "repeated a verb back-to-back"
    assert set(seq) == set(VERBS), "some verbs never appear"
    print("ok")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--interval", type=float, default=0.6, help="seconds per verb")
    p.add_argument("--once", action="store_true", help="print one verb and exit")
    p.add_argument("--selftest", action="store_true", help="run internal check")
    a = p.parse_args()
    if a.selftest:
        demo()
    elif a.once:
        print(random.choice(VERBS))
    else:
        spin(a.interval)


if __name__ == "__main__":
    main()
