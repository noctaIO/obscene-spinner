# obscene-spinner

```bash
brew tap noctaIO/tap
brew install obscene-spinner
spin
```

That puts `spin` (and `obscene-spinner`) on your PATH. Newer Homebrew gates third-party taps, so if it asks, run `brew trust noctaio/tap` first.

Or skip Homebrew — it's one Python 3 file, no pip, no build, no config:

```bash
git clone https://github.com/noctaIO/obscene-spinner
cd obscene-spinner
./spin.py
```

Ctrl-C to stop.

The point isn't to watch it spin in a window — it's to drive the spinner Claude Code shows **while it's working on your prompt**. So run it bare and you get an arrow-key menu that **sets your real spinner** to whichever pack you pick: profanity or live news (still one file, still no dependencies — that's stdlib `curses`).

```bash
./spin.py                 # picker → applies your choice to ~/.claude/settings.json
./spin.py --verbs         # just watch the verb animation, ~0.6s each
./spin.py --news          # just watch the live-headline animation
./spin.py --set news      # apply a pack straight away, no menu (verbs|news|toggle)
./spin.py --status        # which pack is active right now
./spin.py --interval 0.3  # faster
./spin.py --once          # one verb and quit, handy for status lines
```

`--set` is the no-menu path: wire it to a shell alias or a Claude Code `/spinner` command and flip packs in one keystroke.

Picking **profanity** writes the eighty verbs into your `spinnerVerbs`; picking **live news** writes the latest markets headlines and a background poller keeps them fresh. The `--verbs` / `--news` flags don't touch your settings — they just run the standalone animation so you can preview a pack.

![the spinner cycling through the pack](demo.svg)

Claude Code lets you swap out its spinner verbs. The stock set is whimsical: "Noodling", "Lollygagging", "Shenaniganing". Pleasant. A little pleased with itself.

This is the other set. The one a senior dev mutters at 2am when the build is on fire: `faffing`, `unfucking`, `yak-shaving`, `yeeting-to-prod`, `polishing-a-turd`, `crashing-out`. Eighty of them.

There's a catch with the real spinner. It only swaps a verb when a new operation starts, so a whole session shows you maybe five of the eighty and none of the good ones. This one runs them on a timer instead, fast enough that the full pack actually goes past.

## Put it in your real spinner

The picker does this for you — pick a pack and it writes `spinnerVerbs` into `~/.claude/settings.json` (mode `"replace"`, other settings untouched). If you'd rather do it by hand:

```json
{
  "spinnerVerbs": { "mode": "replace", "verbs": ["faffing", "unfucking", "..."] }
}
```

The whole list is the `VERBS` array in [`spin.py`](spin.py); copy it straight across. The left-hand spinner glyph animations are in `PLAINTEXT_ANIMATIONS` (same file) if you want to reuse those too. Fair warning: `"replace"` throws out the polite defaults, so this is not the version you want on screen during a demo.

## News mode

Same spinner, different pack: live news headlines instead of verbs. Pick **live news** in the menu and it becomes your Claude Code spinner. Out of the box it reads a live **markets wire** — the top Reuters market stories, refreshed every few minutes — one headline at a time, with the spinner animating on the left.

```bash
./spin.py --news                       # preview: ~5s per headline, refreshes in the background
./spin.py --news --interval 1.2        # faster churn
./spin.py --news --news-url URL        # or set SPIN_NEWS_URL — any {"items": [...]} JSON
```

**Read the story:** while the ticker runs, press `n` to pause and read the current headline's summary, then any key to resume (`q` quits). One catch — the summary only works in this standalone ticker, not in Claude Code's own spinner, which Claude Code draws itself.

Each headline is measured by real screen width — Chinese text and emoji count as two columns, accents as zero — then filled right to the edge and capped with `…`, recomputed every frame. So it never overflows or wraps, packs the line tight, and re-fits the instant you resize. Headlines are reshuffled like a deck, so you see the whole feed before any repeat.

Still Python 3 and nothing else — the feed is fetched with the standard library. The default is the markets wire, but point `--news-url` (or `SPIN_NEWS_URL`) at any small JSON endpoint shaped like `{"items": ["headline", ...]}` and it'll spin whatever you feed it. No network, no feed? It says so and exits instead of hanging.

## Status-line news marquee

The spinner has a catch for news: Claude Code only swaps the spinner verb when a
new operation starts, so a headline can sit frozen for a long quiet wait — it
never "continues the story." The status line doesn't have that limit. `spin.py`
writes the full feed (titles **and** summaries) to a cache, and
[`newscrawl.js`](newscrawl.js) renders it as a horizontally-scrolling ticker you
can drop into Claude Code's `statusLine`.

```jsonc
// ~/.claude/settings.json
{
  "statusLine": {
    "type": "command",
    "command": "node /path/to/obscene-spinner/newscrawl.js --render"
  }
}
```

The scroll position is derived from wall-clock time (`offset = floor(now/step)`),
not a saved counter. Claude Code re-invokes the status-line command on its own
(irregular) schedule and re-spawns it fresh each time — deriving the frame from
the clock means every invocation independently draws the correct frame for
"now", so an irregular cadence just lowers the framerate, it never desyncs the
crawl. The renderer is a pure reader: it never touches the network, so it stays
well under the status-line timeout.

Keep the cache fresh with a scheduler (the renderer never fetches):

```bash
spin.py --refresh-cache          # fetch feed -> cache, no settings touched
# then run it on a timer, e.g. cron:  */3 * * * *  spin.py --refresh-cache
# Windows Task Scheduler:  schtasks /create /tn obscene-spinner-news \
#   /tr "py C:\path\spin.py --refresh-cache" /sc minute /mo 3
```

Tuning (env vars, read by `newscrawl.js` / the status-line reader):
`SPIN_STATUS_WIDTH` (visible columns, default 44), `SPIN_STATUS_STEP_MS`
(ms per column, default 250), `SPIN_STATUS_MAX_AGE_MS` (hide news older than
this, default 30 min), `SPIN_STATUS_NEWS=0` to disable. Watch it live with
`node newscrawl.js --demo`.

## The verbs

Roughly four moods, because a bad session moves through all of them:

- **British disasters** — faffing, bollocksing, fannying-about, going-tits-up, cocking-it-up
- **Cope** — malding, seething, huffing-copium, dissociating, crashing-out
- **Honest engineering** — yak-shaving, bikeshedding, monkeypatching, footgunning, kludging
- **Do not do this in prod** — yeeting-to-prod, force-pushing-to-main, rm-rf-ing, shipping-and-praying

A single frame, if the animation above won't play in your viewer:

![one frame of the spinner](still.svg)

The stock verbs are fine. But sometimes the honest status really is `bodging`, and the terminal may as well say so.
