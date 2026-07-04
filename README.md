# obscene-spinner

The crude Claude Code spinner-verb pack, as a standalone toy. 80 verbs a senior
dev actually mutters while the AI runs — `faffing`, `unfucking`, `yak-shaving`,
`yeeting-to-prod`, `polishing-a-turd`, `crashing-out`.

The real Claude Code spinner only swaps a verb when a new operation starts, so
most of the pack never shows. This cycles fast on a timer so you see them all.

```bash
./spin.py                 # ~0.6s per verb
./spin.py --interval 0.3  # faster
./spin.py --once          # print one random verb (for scripts / status lines)
```

No dependencies, stdlib only. Ctrl-C to quit.

## Use it as your actual Claude Code spinner

Drop the pack into `~/.claude/settings.json`:

```json
{
  "spinnerVerbs": { "mode": "replace", "verbs": ["faffing", "unfucking", "..."] }
}
```

The full list is the `VERBS` array in [`spin.py`](spin.py). ⚠️ `replace` mode
swaps the default whimsical verbs for these — not screenshot-safe for work.
