# obscene-spinner

```bash
git clone https://github.com/noctaIO/obscene-spinner
cd obscene-spinner
./spin.py
```

Python 3 and nothing else. No pip, no build, no config file. Ctrl-C to stop.

```bash
./spin.py                 # ~0.6s per verb
./spin.py --interval 0.3  # faster
./spin.py --once          # one verb and quit, handy for status lines
./spin.py --news          # spin live wire headlines instead of verbs
```

![the spinner cycling through the pack](demo.svg)

Claude Code lets you swap out its spinner verbs. The stock set is whimsical: "Noodling", "Lollygagging", "Shenaniganing". Pleasant. A little pleased with itself.

This is the other set. The one a senior dev mutters at 2am when the build is on fire: `faffing`, `unfucking`, `yak-shaving`, `yeeting-to-prod`, `polishing-a-turd`, `crashing-out`. Eighty of them.

There's a catch with the real spinner. It only swaps a verb when a new operation starts, so a whole session shows you maybe five of the eighty and none of the good ones. This one runs them on a timer instead, fast enough that the full pack actually goes past.

## Put it in your real spinner

Drop the pack into `~/.claude/settings.json`:

```json
{
  "spinnerVerbs": { "mode": "replace", "verbs": ["faffing", "unfucking", "..."] }
}
```

The whole list is the `VERBS` array in [`spin.py`](spin.py); copy it straight across. Fair warning: `"replace"` throws out the polite defaults, so this is not the version you want on screen during a demo.

## News mode

Same spinner, different pack. `--news` swaps the eighty verbs for live wire headlines, one at a time, each trimmed to fit your terminal — recomputed every frame, so it re-fits when you resize the window.

```bash
./spin.py --news                       # ~2.5s per headline, refreshes in the background
./spin.py --news --interval 1.2        # faster churn
./spin.py --news --news-url URL        # or set SPIN_NEWS_URL — any {"items": [...]} JSON
```

Still Python 3 and nothing else — the feed is fetched with the standard library. It reads a small JSON endpoint (`{"items": ["headline", ...]}`); point it at your own and it'll spin anything you like. No network, no feed? It says so and exits instead of hanging.

## The verbs

Roughly four moods, because a bad session moves through all of them:

- **British disasters** — faffing, bollocksing, fannying-about, going-tits-up, cocking-it-up
- **Cope** — malding, seething, huffing-copium, dissociating, crashing-out
- **Honest engineering** — yak-shaving, bikeshedding, monkeypatching, footgunning, kludging
- **Do not do this in prod** — yeeting-to-prod, force-pushing-to-main, rm-rf-ing, shipping-and-praying

A single frame, if the animation above won't play in your viewer:

![one frame of the spinner](still.svg)

The stock verbs are fine. But sometimes the honest status really is `bodging`, and the terminal may as well say so.
