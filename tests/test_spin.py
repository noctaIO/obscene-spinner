"""Tests for obscene-spinner.

Stdlib unittest, no pytest — the whole point of the project is that it runs
anywhere Python 3 does, and the tests should hold to the same bar.

    python3 -m unittest discover -s tests -v
    ./spin.py --selftest

Nothing here touches the network or the real ~/.claude: every filesystem test
runs in a tempdir, and the one live pack is stubbed.
"""
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import spin  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SPIN_PY = os.path.join(os.path.dirname(HERE), "spin.py")

# Headlines that broke the codepoint-based implementation: wide CJK, emoji,
# NFD-decomposed accents, and a word with nowhere to break.
HARD_TEXT = [
    "Kremlin says Putin held a US-initiated phone call with President Trump today",
    "中国A股上证指数创下新高投资者观望美联储利率决议",
    "Markets rip higher 🚀 as the Fed signals a surprise pause on rate hikes",
    unicodedata.normalize(
        "NFD", "Café señor naïve résumé façade — accented world news roundup"),
    "x" * 120,
]


def setUpModule():
    """Point user-pack loading at an empty dir so the developer's own custom
    packs can't change what the tests see."""
    global _tmp
    _tmp = tempfile.mkdtemp(prefix="spin-tests-")
    spin.USER_PACK_DIR = os.path.join(_tmp, "no-such-dir")
    spin.all_packs(refresh=True)


# ---------------------------------------------------------------------------


class TestWidths(unittest.TestCase):
    def test_char_width(self):
        self.assertEqual(spin.char_width("a"), 1)
        self.assertEqual(spin.char_width("中"), 2)
        self.assertEqual(spin.char_width("🌕"), 2)
        self.assertEqual(spin.char_width("́"), 0)   # combining acute
        self.assertEqual(spin.char_width("​"), 0)   # zero-width space

    def test_disp_width_counts_columns_not_codepoints(self):
        nfd = unicodedata.normalize("NFD", "café")
        self.assertEqual(len(nfd), 5)
        self.assertEqual(spin.disp_width(nfd), 4)

    def test_pad_and_rpad_use_columns(self):
        self.assertEqual(spin.disp_width(spin.pad("中文", 10)), 10)
        self.assertEqual(spin.disp_width(spin.rpad("中文", 10)), 10)
        self.assertTrue(spin.rpad("x", 4).startswith("   "))
        self.assertEqual(spin.pad("toolongvalue", 4), "toolongvalue")

    def test_fit_never_overflows_at_any_prefix(self):
        # The contract: prefix + disp_width(fit(...)) <= width - 1, so the last
        # cell is never written and the terminal cannot auto-wrap.
        for text in HARD_TEXT:
            for width in (6, 8, 10, 20, 40, 56, 80, 120, 200):
                for prefix in (0, 1, 2, 3, 5):
                    out = spin.fit(text, width, prefix)
                    self.assertLessEqual(
                        prefix + spin.disp_width(out), width - 1,
                        "prefix=%d width=%d text=%r out=%r"
                        % (prefix, width, text[:20], out))
                    if out and out != text:
                        self.assertTrue(out.endswith("…"), repr(out))

    def test_fit_gives_up_rather_than_overflowing_a_hopeless_width(self):
        # A 5-column glyph in a 6-column window leaves no room for text at all;
        # an ellipsis would itself break the guarantee.
        self.assertEqual(spin.fit("anything", 6, prefix=5), "")
        self.assertEqual(spin.fit("anything", 1, prefix=0), "")

    def test_fit_leaves_short_text_alone(self):
        self.assertEqual(spin.fit("short", 80), "short")
        self.assertEqual(spin.fit("short", 80, prefix=5), "short")

    def test_fit_keeps_combining_marks_with_their_base(self):
        nfd = unicodedata.normalize("NFD", "éééééééééé")
        out = spin.fit(nfd, 8)
        self.assertFalse(out.startswith("́"))

    def test_plural(self):
        self.assertEqual(spin.plural(1, "verb"), "1 verb")
        self.assertEqual(spin.plural(0, "verb"), "0 verbs")
        self.assertEqual(spin.plural(2, "verb"), "2 verbs")


class TestShuffleBag(unittest.TestCase):
    def test_deals_the_whole_deck_before_repeating(self):
        pool = [p["id"] for p in spin.PACKS]
        g = spin.shuffle_bag(pool)
        seq = [next(g) for _ in range(2000)]
        n = len(pool)
        self.assertTrue(all(a != b for a, b in zip(seq, seq[1:])),
                        "repeated back-to-back")
        self.assertEqual(set(seq), set(pool))
        self.assertEqual(len(set(seq[:n])), n, "first cycle must be a full sweep")
        self.assertEqual(len(set(seq[n:2 * n])), n, "so must the second")

    def test_single_item_pool_does_not_hang(self):
        g = spin.shuffle_bag(["only"])
        self.assertEqual([next(g) for _ in range(3)], ["only"] * 3)

    def test_stable_order_is_deterministic_and_total(self):
        items = list("abcdefgh")
        a = spin.stable_order("pack", items)
        b = spin.stable_order("pack", items)
        c = spin.stable_order("other", items)
        self.assertEqual(a, b)
        self.assertEqual(sorted(a), items)
        self.assertNotEqual(a, c)


class TestSpinners(unittest.TestCase):
    def test_every_frame_in_a_set_has_the_same_display_width(self):
        # If they differ the verb beside the glyph jumps a column each tick.
        for sid, sp in spin.SPINNERS.items():
            widths = set(spin.disp_width(f) for f in sp["frames"])
            self.assertEqual(len(widths), 1,
                             "%s frames have mixed widths: %s" % (sid, widths))
            self.assertGreater(widths.pop(), 0, sid)

    def test_frames_non_empty_and_interval_positive(self):
        for sid, sp in spin.SPINNERS.items():
            self.assertTrue(sp["frames"], sid)
            self.assertGreater(sp["interval"], 0, sid)
            self.assertTrue(sp["name"].strip(), sid)

    def test_frame_at_is_a_pure_function_of_the_clock(self):
        sp = spin.SPINNERS["braille"]
        n, iv = len(sp["frames"]), sp["interval"]
        self.assertEqual(spin.frame_at("braille", 0), sp["frames"][0])
        self.assertEqual(spin.frame_at("braille", iv * 1.5), sp["frames"][1])
        # A full lap returns to the start.
        self.assertEqual(spin.frame_at("braille", iv * n),
                         spin.frame_at("braille", 0))
        self.assertEqual(spin.frame_at("braille", 3.0),
                         spin.frame_at("braille", 3.0))

    def test_frame_at_visits_every_frame(self):
        sp = spin.SPINNERS["arc"]
        seen = set(spin.frame_at("arc", i * sp["interval"])
                   for i in range(len(sp["frames"]) * 2))
        self.assertEqual(seen, set(sp["frames"]))

    def test_unknown_spinner_falls_back_instead_of_raising(self):
        self.assertEqual(spin.get_spinner("nope"),
                         spin.SPINNERS[spin.DEFAULT_SPINNER])
        self.assertEqual(spin.frame_width("nope"),
                         spin.frame_width(spin.DEFAULT_SPINNER))

    def test_default_spinner_exists(self):
        self.assertIn(spin.DEFAULT_SPINNER, spin.SPINNERS)


class TestThemes(unittest.TestCase):
    def test_every_theme_is_well_formed(self):
        for tid, th in spin.THEMES.items():
            for role in ("frame", "accent", "text"):
                rgb = th[role]
                self.assertEqual(len(rgb), 3, tid)
                for c in rgb:
                    self.assertTrue(0 <= c <= 255, (tid, role, rgb))
            self.assertIn(th.get("style", "solid"),
                          ("solid", "gradient", "rainbow"), tid)

    def test_theme_rgb_stays_in_range_across_the_whole_cycle(self):
        for tid in spin.THEMES:
            for i in range(41):
                rgb = spin.theme_rgb(tid, "frame", i / 40.0)
                self.assertEqual(len(rgb), 3)
                for c in rgb:
                    self.assertTrue(0 <= c <= 255, (tid, i, rgb))

    def test_gradient_pingpongs_so_there_is_no_seam(self):
        rgb = spin.THEMES["ember"]
        self.assertEqual(spin.theme_rgb("ember", "frame", 0.0),
                         tuple(rgb["frame"]))
        self.assertEqual(spin.theme_rgb("ember", "frame", 1.0),
                         tuple(rgb["frame"]))
        self.assertEqual(spin.theme_rgb("ember", "frame", 0.5),
                         tuple(rgb["accent"]))

    def test_solid_theme_ignores_phase(self):
        a = spin.theme_rgb("acid", "frame", 0.0)
        b = spin.theme_rgb("acid", "frame", 0.7)
        self.assertEqual(a, b)

    def test_rgb_to_256_in_range(self):
        for rgb in [(0, 0, 0), (255, 255, 255), (128, 128, 128),
                    (255, 0, 0), (12, 200, 77), (7, 7, 7), (250, 250, 250)]:
            idx = spin.rgb_to_256(rgb)
            self.assertTrue(16 <= idx <= 255, (rgb, idx))

    def test_rgb_to_16_in_range_and_never_invisible(self):
        for rgb in [(0, 0, 0), (255, 255, 255), (200, 200, 200), (10, 10, 10)]:
            idx = spin.rgb_to_16(rgb)
            self.assertTrue(0 <= idx <= 15, (rgb, idx))
        # A bright grey must not collapse to black-on-black.
        self.assertNotEqual(spin.rgb_to_16((200, 200, 200)), 8)

    def test_color_mode_detection(self):
        class FakeTTY:
            def isatty(self):
                return True

        class FakePipe:
            def isatty(self):
                return False

        tty, pipe = FakeTTY(), FakePipe()
        self.assertEqual(spin.color_mode(pipe, env={"TERM": "xterm-256color"}),
                         "none")
        self.assertEqual(spin.color_mode(tty, env={"TERM": "xterm-256color",
                                                   "NO_COLOR": "1"}), "none")
        self.assertEqual(spin.color_mode(tty, env={"TERM": "dumb"}), "none")
        self.assertEqual(spin.color_mode(tty, env={}), "none")
        self.assertEqual(spin.color_mode(tty, env={"TERM": "xterm"}), "16")
        self.assertEqual(spin.color_mode(tty, env={"TERM": "xterm-256color"}),
                         "256")
        self.assertEqual(spin.color_mode(tty, env={"TERM": "xterm-256color",
                                                   "COLORTERM": "truecolor"}),
                         "truecolor")
        self.assertEqual(spin.color_mode(pipe, force="truecolor"), "truecolor")

    def test_sgr_shapes(self):
        self.assertEqual(spin.sgr((1, 2, 3), "none"), "")
        self.assertEqual(spin.sgr((1, 2, 3), "truecolor"), "\033[38;2;1;2;3m")
        self.assertTrue(spin.sgr((1, 2, 3), "256").startswith("\033[38;5;"))
        self.assertTrue(spin.sgr((1, 2, 3), "16").startswith("\033["))
        self.assertTrue(spin.sgr((1, 2, 3), "truecolor", bold=True)
                        .startswith("\033[1;"))

    def test_render_line_emits_no_escapes_without_colour(self):
        line = spin.render_line("⠋", "faffing", "acid", "none")
        self.assertEqual(line, "⠋ faffing…")
        self.assertNotIn("\033", line)

    def test_render_line_colours_and_resets(self):
        for m in ("truecolor", "256", "16"):
            line = spin.render_line("⠋", "faffing", "acid", m)
            self.assertIn("\033", line)
            self.assertTrue(line.endswith(spin.RESET))
            self.assertIn("faffing", line)

    def test_unknown_theme_falls_back(self):
        self.assertEqual(spin.get_theme("nope"), spin.THEMES[spin.DEFAULT_THEME])


class TestPackRegistry(unittest.TestCase):
    def test_ids_are_unique_and_well_formed(self):
        ids = [p["id"] for p in spin.PACKS]
        self.assertEqual(len(ids), len(set(ids)), "duplicate pack ids")
        for pid in ids:
            self.assertRegex(pid, r"^[a-z0-9][a-z0-9_-]*$")

    def test_every_pack_declares_the_fields_the_ui_reads(self):
        for p in spin.PACKS:
            for key in ("id", "name", "desc", "category", "rating", "spinner",
                        "theme"):
                self.assertIn(key, p, p["id"])
                self.assertTrue(str(p[key]).strip(), (p["id"], key))
            self.assertIn(p["rating"], spin.RATINGS, p["id"])
            self.assertIn(p["spinner"], spin.SPINNERS, p["id"])
            self.assertIn(p["theme"], spin.THEMES, p["id"])
            self.assertIn(p["category"], spin.CATEGORIES, p["id"])

    def test_every_verb_fits_the_line_claude_code_draws(self):
        for p in spin.PACKS:
            for v in p.get("verbs", []):
                self.assertGreater(spin.disp_width(v), 0, p["id"])
                self.assertLessEqual(spin.disp_width(v), spin.SPINNER_MAX,
                                     "%s: %r" % (p["id"], v))
                self.assertEqual(v, v.strip(), "%s: %r" % (p["id"], v))
                self.assertNotIn("\n", v)

    def test_no_duplicate_verbs_within_a_pack(self):
        for p in spin.PACKS:
            verbs = p.get("verbs", [])
            dupes = set(v for v in verbs if verbs.count(v) > 1)
            self.assertFalse(dupes, "%s repeats %s" % (p["id"], sorted(dupes)))

    def test_static_packs_are_substantial(self):
        for p in spin.PACKS:
            if p.get("kind"):
                continue
            self.assertGreaterEqual(len(p["verbs"]), 30,
                                    "%s is thin" % p["id"])

    def test_there_are_enough_sfw_packs_to_be_useful(self):
        sfw = [p for p in spin.PACKS if p["rating"] == "sfw"]
        self.assertGreaterEqual(len(sfw), 8)

    def test_every_category_has_a_pack(self):
        used = set(p["category"] for p in spin.PACKS)
        self.assertEqual(used, set(spin.CATEGORIES))

    def test_get_pack_by_id_and_unique_prefix(self):
        self.assertEqual(spin.get_pack("pirate")["id"], "pirate")
        self.assertEqual(spin.get_pack("pir")["id"], "pirate")
        self.assertIsNone(spin.get_pack("definitely-not-a-pack"))
        # 'c' matches corporate/chef/commentator/chaos/cope -> ambiguous
        self.assertIsNone(spin.get_pack("c"))

    def test_resolve_verbs_static(self):
        p = spin.get_pack("zen")
        self.assertEqual(spin.resolve_verbs(p), p["verbs"])
        # a copy, not the registry's own list
        spin.resolve_verbs(p).append("mutated")
        self.assertNotIn("mutated", p["verbs"])

    def test_resolve_verbs_meta_pack_samples_widely(self):
        verbs = spin.resolve_verbs(spin.get_pack("chaos"))
        self.assertTrue(verbs)
        self.assertEqual(len(verbs), len(set(verbs)), "chaos repeats itself")
        for v in verbs:
            self.assertLessEqual(spin.disp_width(v), spin.SPINNER_MAX)
        # drawn from more than one source pack
        sources = set()
        for p in spin.PACKS:
            if set(p.get("verbs", [])) & set(verbs):
                sources.add(p["id"])
        self.assertGreater(len(sources), 5)

    def test_resolve_verbs_live_pack_uses_the_feed(self):
        real = spin.fetch_news
        spin.fetch_news = lambda url: [
            {"title": "Fed holds rates steady as inflation cools — Reuters",
             "summary": None}]
        try:
            verbs = spin.resolve_verbs(spin.get_pack("news"), "http://example")
        finally:
            spin.fetch_news = real
        self.assertEqual(verbs, ["Fed holds rates steady as inflation cools"])

    def test_sample_verbs_for_a_live_pack_never_hits_the_network(self):
        real = spin.fetch_news

        def explode(url):
            raise AssertionError("sample_verbs must not fetch")

        spin.fetch_news = explode
        try:
            self.assertTrue(spin.sample_verbs(spin.get_pack("news")))
        finally:
            spin.fetch_news = real

    def test_verb_at_is_stable_and_cycles(self):
        p = spin.get_pack("zen")
        self.assertEqual(spin.verb_at(p, 0.0), spin.verb_at(p, 0.0))
        self.assertIn(spin.verb_at(p, 7.3), p["verbs"])
        # Sample mid-dwell: exactly on a boundary, float division can land
        # either side of the step.
        seen = set(spin.verb_at(p, (i + 0.5) * spin.VERB_DWELL)
                   for i in range(len(p["verbs"])))
        self.assertEqual(seen, set(p["verbs"]))


class TestCondense(unittest.TestCase):
    def test_every_headline_lands_inside_the_line(self):
        extra = [
            "Fed holds rates steady as inflation cools, Powell signals "
            "patience — Reuters",
            "United States and European Union agree billion-dollar trade "
            "framework",
        ]
        for text in HARD_TEXT + extra:
            c = spin.condense(text, spin.SPINNER_MAX)
            self.assertTrue(0 < spin.disp_width(c) <= spin.SPINNER_MAX,
                            (repr(text[:30]), repr(c)))

    def test_strips_the_source_tag(self):
        self.assertEqual(
            spin.condense("Stocks rise on tech rally — Bloomberg", 56),
            "Stocks rise on tech rally")
        self.assertNotIn("reuters", spin.condense(
            "Oil slides as OPEC+ weighs output hike next quarter — Reuters",
            56).lower())

    def test_leaves_a_short_headline_untouched(self):
        self.assertEqual(spin.condense("Short headline", 56), "Short headline")

    def test_abbreviates_before_truncating(self):
        out = spin.condense(
            "United States government spends 40 percent more than "
            "European Union", 56)
        self.assertIn("US", out)
        self.assertNotIn("percent", out)

    def test_every_builtin_verb_survives_condensing_unchanged(self):
        for p in spin.PACKS:
            for v in p.get("verbs", []):
                self.assertEqual(spin.condense(v, spin.SPINNER_MAX), v,
                                 "%s: %r" % (p["id"], v))


class TestNormalizeItems(unittest.TestCase):
    def test_accepts_strings_and_objects(self):
        self.assertEqual(spin.normalize_items(["a", "b"]),
                         [{"title": "a", "summary": None},
                          {"title": "b", "summary": None}])
        self.assertEqual(spin.normalize_items([{"title": "T", "summary": "S"}]),
                         [{"title": "T", "summary": "S"}])
        self.assertEqual(
            spin.normalize_items([{"title": "T", "description": "D"}])[0]["summary"],
            "D")

    def test_drops_empty_titles(self):
        self.assertEqual(spin.normalize_items([{"title": ""}, "  ", None]), [])

    def test_handles_none(self):
        self.assertEqual(spin.normalize_items(None), [])


class TestValidatePack(unittest.TestCase):
    def test_accepts_a_minimal_pack(self):
        pack, notes = spin.validate_pack({"id": "mine", "verbs": ["fiddling"]})
        self.assertIsNotNone(pack)
        self.assertEqual(notes, [])
        self.assertEqual(pack["verbs"], ["fiddling"])
        self.assertEqual(pack["rating"], "sfw")
        self.assertTrue(pack["custom"])

    def test_rejects_the_unusable(self):
        for raw in ({}, {"id": "x"}, {"verbs": ["a"]}, {"id": "Bad Id",
                                                        "verbs": ["a"]},
                    {"id": "ok", "verbs": "notalist"},
                    {"id": "ok", "verbs": []}, "not a dict", 42):
            pack, msgs = spin.validate_pack(raw)
            self.assertIsNone(pack, repr(raw))
            self.assertTrue(msgs, repr(raw))

    def test_recovers_from_a_bad_field_instead_of_binning_the_pack(self):
        pack, notes = spin.validate_pack({
            "id": "mine", "verbs": ["fiddling", "x" * 90, 7, "  ", "poking"],
            "rating": "spicy", "spinner": "nope", "theme": "nope"})
        self.assertIsNotNone(pack)
        self.assertEqual(pack["verbs"], ["fiddling", "poking"])
        self.assertEqual(pack["rating"], "sfw")
        self.assertEqual(pack["spinner"], spin.DEFAULT_SPINNER)
        self.assertEqual(pack["theme"], spin.DEFAULT_THEME)
        self.assertEqual(len(notes), 5)

    def test_a_pack_of_only_bad_verbs_is_fatal(self):
        pack, msgs = spin.validate_pack({"id": "mine", "verbs": ["x" * 90]})
        self.assertIsNone(pack)
        self.assertTrue(any("no usable verbs" in m for m in msgs))


class TestUserPacks(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="spin-userpacks-")

    def write(self, name, obj):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            if isinstance(obj, str):
                f.write(obj)
            else:
                json.dump(obj, f)
        return path

    def test_loads_valid_packs(self):
        self.write("a.json", {"id": "aaa", "verbs": ["one", "two"]})
        self.write("b.json", [{"id": "bbb", "verbs": ["three"]}])
        packs, errors = spin.load_user_packs(self.dir)
        self.assertEqual(sorted(p["id"] for p in packs), ["aaa", "bbb"])
        self.assertEqual(errors, [])

    def test_broken_files_are_reported_not_fatal(self):
        self.write("good.json", {"id": "good", "verbs": ["one"]})
        self.write("broken.json", "this is not json")
        self.write("bad.json", {"id": "Bad Id", "verbs": []})
        packs, errors = spin.load_user_packs(self.dir)
        self.assertEqual([p["id"] for p in packs], ["good"])
        self.assertGreaterEqual(len(errors), 2)

    def test_missing_directory_is_fine(self):
        packs, errors = spin.load_user_packs(os.path.join(self.dir, "nope"))
        self.assertEqual((packs, errors), ([], []))

    def test_a_user_pack_can_override_a_builtin(self):
        self.write("profanity.json", {"id": "profanity", "name": "Mine",
                                      "verbs": ["replaced"]})
        old = spin.USER_PACK_DIR
        spin.USER_PACK_DIR = self.dir
        try:
            packs = spin.all_packs(refresh=True)
            got = dict((p["id"], p) for p in packs)["profanity"]
            self.assertEqual(got["verbs"], ["replaced"])
            self.assertEqual(got["name"], "Mine")
            # order is preserved: it stays where the builtin was
            self.assertEqual(packs[0]["id"], "profanity")
            self.assertEqual(len(packs), len(spin.PACKS))
        finally:
            spin.USER_PACK_DIR = old
            spin.all_packs(refresh=True)


class TestApplyAndRestore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="spin-apply-")
        self.settings = os.path.join(self.dir, "settings.json")
        self.mode = os.path.join(self.dir, "spinner-mode")
        self.backup = os.path.join(self.dir, "spinner-backup.json")
        self.state = os.path.join(self.dir, "spinner-state.json")

    def write_settings(self, obj):
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def read_settings(self):
        with open(self.settings, encoding="utf-8") as f:
            return json.load(f)

    def apply(self, verbs, mode="verbs", state=None):
        spin.apply_pack(verbs, mode, self.settings, self.mode, self.backup,
                        state, self.state)

    def test_writes_spinner_verbs_without_touching_anything_else(self):
        self.write_settings({"theme": "dark", "nested": {"deep": True}})
        self.apply(["a", "b"], "news")
        got = self.read_settings()
        self.assertEqual(got["theme"], "dark")
        self.assertEqual(got["nested"], {"deep": True})
        self.assertEqual(got["spinnerVerbs"],
                         {"mode": "replace", "verbs": ["a", "b"]})
        with open(self.mode, encoding="utf-8") as f:
            self.assertEqual(f.read(), "news")

    def test_works_when_settings_do_not_exist(self):
        self.apply(["a"])
        self.assertEqual(self.read_settings()["spinnerVerbs"]["verbs"], ["a"])

    def test_survives_a_corrupt_settings_file(self):
        with open(self.settings, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.apply(["a"])
        self.assertEqual(self.read_settings()["spinnerVerbs"]["verbs"], ["a"])

    def test_restore_returns_the_original_bytes(self):
        original = {"theme": "dark",
                    "spinnerVerbs": {"mode": "append", "verbs": ["mine"]},
                    "other": 1}
        self.write_settings(original)
        self.apply(["a"])
        self.apply(["b"])          # a second apply must not move the backup
        self.apply(["c"])
        msg = spin.restore_pack(self.settings, self.backup, self.mode,
                                self.state)
        self.assertIn("restored", msg)
        self.assertEqual(self.read_settings(), original)

    def test_restore_removes_the_key_when_there_was_none(self):
        self.write_settings({"theme": "dark"})
        self.apply(["a"])
        spin.restore_pack(self.settings, self.backup, self.mode, self.state)
        self.assertEqual(self.read_settings(), {"theme": "dark"})
        self.assertNotIn("spinnerVerbs", self.read_settings())

    def test_restore_clears_its_own_bookkeeping(self):
        self.apply(["a"], state={"pack": "zen"})
        spin.restore_pack(self.settings, self.backup, self.mode, self.state)
        for path in (self.backup, self.state, self.mode):
            self.assertFalse(os.path.exists(path), path)

    def test_restore_with_no_backup_says_so(self):
        msg = spin.restore_pack(self.settings, self.backup, self.mode,
                                self.state)
        self.assertIn("nothing to restore", msg)

    def test_backup_once_is_idempotent(self):
        self.write_settings({"spinnerVerbs": {"verbs": ["first"]}})
        self.assertTrue(spin.backup_once(self.settings, self.backup))
        self.write_settings({"spinnerVerbs": {"verbs": ["second"]}})
        self.assertFalse(spin.backup_once(self.settings, self.backup))
        with open(self.backup, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["spinnerVerbs"]["verbs"], ["first"])

    def test_state_records_the_previous_pack_for_toggle(self):
        self.apply(["a"], state={"pack": "zen", "spinner": "static",
                                 "theme": "bone"})
        self.apply(["b"], state={"pack": "pirate", "spinner": "arc",
                                 "theme": "gold"})
        with open(self.state, encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(st["pack"], "pirate")
        self.assertEqual(st["previous"], "zen")
        self.assertIn("applied_at", st)

    def test_the_write_is_atomic_and_leaves_no_temp_file(self):
        self.apply(["a"])
        self.assertFalse(os.path.exists(self.settings + ".tmp"))

    def test_legacy_aliases_still_map_to_packs(self):
        self.assertEqual(spin.LEGACY_ALIASES["verbs"], "profanity")
        self.assertEqual(spin.LEGACY_ALIASES["news"], "news")


class TestGalleryState(unittest.TestCase):
    def setUp(self):
        self.st = spin.GalleryState(packs=spin.all_packs())

    def test_starts_showing_everything(self):
        self.assertEqual(len(self.st.filtered()), len(spin.PACKS))
        self.assertEqual(self.st.current()["id"], "profanity")

    def test_sfw_filter(self):
        self.st.toggle_sfw()
        rows = self.st.filtered()
        self.assertTrue(rows)
        self.assertTrue(all(p["rating"] == "sfw" for p in rows))
        self.assertLess(len(rows), len(spin.PACKS))
        self.st.toggle_sfw()
        self.assertEqual(len(self.st.filtered()), len(spin.PACKS))

    def test_search_matches_name_desc_and_verbs(self):
        self.st.set_query("pirate")
        self.assertEqual([p["id"] for p in self.st.filtered()], ["pirate"])
        self.st.set_query("keelhaulin")            # a verb, not a name
        self.assertEqual([p["id"] for p in self.st.filtered()], ["pirate"])
        self.st.set_query("zzzznotathing")
        self.assertEqual(self.st.filtered(), [])
        self.assertIsNone(self.st.current())

    def test_search_resets_the_cursor(self):
        self.st.move(5)
        self.st.set_query("zen")
        self.assertEqual(self.st.idx, 0)

    def test_category_cycles_through_all_and_back(self):
        seen = []
        for _ in range(len(spin.CATEGORIES) + 1):
            self.st.cycle_category(1)
            seen.append(self.st.category)
        self.assertIn(None, seen)
        for cat in spin.CATEGORIES:
            self.assertIn(cat, seen)
        self.st.category = "fiction"
        self.assertTrue(all(p["category"] == "fiction"
                            for p in self.st.filtered()))

    def test_movement_wraps(self):
        n = len(self.st.filtered())
        self.st.move(-1)
        self.assertEqual(self.st.idx, n - 1)
        self.st.move(1)
        self.assertEqual(self.st.idx, 0)
        self.st.move(n + 3)
        self.assertEqual(self.st.idx, 3)

    def test_movement_on_an_empty_list_is_safe(self):
        self.st.set_query("zzzznotathing")
        self.st.move(1)
        self.st.move(-10)
        self.assertIsNone(self.st.current())
        self.assertIsNone(self.st.selection())

    def test_overrides_cycle_through_none(self):
        self.assertIsNone(self.st.spinner_override)
        for _ in range(len(spin.SPINNERS) + 1):
            self.st.cycle_spinner(1)
        self.assertIsNone(self.st.spinner_override)
        for _ in range(len(spin.THEMES) + 1):
            self.st.cycle_theme(1)
        self.assertIsNone(self.st.theme_override)

    def test_overrides_win_over_pack_defaults(self):
        pack = spin.get_pack("pirate")
        self.assertEqual(self.st.spinner_for(pack), "arc")
        self.st.spinner_override = "moon"
        self.st.theme_override = "matrix"
        self.assertEqual(self.st.spinner_for(pack), "moon")
        self.assertEqual(self.st.theme_for(pack), "matrix")

    def test_selection_shape(self):
        self.st.set_query("wizard")
        sel = self.st.selection()
        self.assertEqual(sel, {"pack": "wizard", "spinner": "star",
                               "theme": "vapor"})

    def test_randomise_lands_somewhere_valid(self):
        for _ in range(30):
            self.st.randomise()
            self.assertIn(self.st.spinner_override, spin.SPINNERS)
            self.assertIn(self.st.theme_override, spin.THEMES)
            self.assertIsNotNone(self.st.current())

    def test_filters_compose(self):
        self.st.toggle_sfw()
        self.st.category = "fiction"
        rows = self.st.filtered()
        self.assertTrue(rows)
        for p in rows:
            self.assertEqual(p["rating"], "sfw")
            self.assertEqual(p["category"], "fiction")


class TestTextScreen(unittest.TestCase):
    def test_writes_at_columns(self):
        scr = spin.TextScreen(3, 10)
        scr.addstr(1, 2, "abc")
        self.assertEqual(scr.text().split("\n")[1], "  abc")

    def test_a_wide_glyph_claims_two_cells(self):
        scr = spin.TextScreen(1, 10)
        scr.addstr(0, 0, "中x")
        self.assertEqual(scr.grid[0][0], "中")
        self.assertEqual(scr.grid[0][1], "")     # continuation
        self.assertEqual(scr.grid[0][2], "x")

    def test_a_combining_mark_joins_the_cell_to_its_left(self):
        scr = spin.TextScreen(1, 10)
        scr.addstr(0, 0, unicodedata.normalize("NFD", "é"))
        self.assertEqual(spin.disp_width(scr.grid[0][0]), 1)

    def test_writes_past_the_edge_are_dropped_not_wrapped(self):
        scr = spin.TextScreen(1, 5)
        scr.addstr(0, 3, "abcdef")
        self.assertEqual(scr.text(), "   ab")

    def test_erase_clears(self):
        scr = spin.TextScreen(2, 4)
        scr.addstr(0, 0, "xxxx")
        scr.erase()
        self.assertEqual(scr.text(), "\n")


class TestGalleryRendering(unittest.TestCase):
    """The layout is the product. Render it at many sizes and assert it holds."""

    SIZES = [(200, 60), (120, 40), (100, 30), (96, 20), (80, 24), (72, 18),
             (62, 14), (58, 12), (40, 10), (30, 8), (20, 6), (12, 5), (4, 3),
             (1, 1)]

    def test_renders_at_every_size_without_raising_or_overflowing(self):
        for w, h in self.SIZES:
            for mode in ("list", "wall", "help"):
                for now in (0.0, 1.7, 12.5):
                    text = spin.render_gallery_text(width=w, height=h, now=now,
                                                    mode=mode)
                    lines = text.split("\n")
                    self.assertLessEqual(len(lines), h, (w, h, mode))
                    for line in lines:
                        self.assertLessEqual(
                            spin.disp_width(line), w,
                            "%dx%d %s overflowed: %r" % (w, h, mode, line))

    def test_the_list_shows_packs_and_the_detail_pane(self):
        text = spin.render_gallery_text(width=100, height=30, now=2.0)
        self.assertIn("obscene-spinner", text)
        self.assertIn("Profanity", text)
        self.assertIn("as Claude Code will draw it", text)
        self.assertIn("┌", text)

    @staticmethod
    def _cells(row):
        """One character per screen cell, so str.find() returns a real column.

        TextScreen stores a wide glyph in one cell and marks the next as a ""
        continuation; joining raw would make the row a character short and
        every column after it read one too low.
        """
        return "".join(c if c else " " for c in row)

    def test_every_pack_name_starts_in_the_same_column(self):
        # Frames run from 1 to 5 columns wide; the names must not move.
        scr = spin.TextScreen(30, 100)
        st = spin.GalleryState(packs=spin.all_packs())
        spin.draw_gallery(scr, spin.Attrs(), st, 3.0)
        names = [p["name"] for p in st.filtered()]
        found = 0
        for y, row in enumerate(scr.grid):
            if y < 3:
                continue
            line = self._cells(row)
            for name in names:
                col = line.find(name)
                if col > 0:
                    self.assertEqual(col, 8, "%r started at column %d"
                                     % (name, col))
                    found += 1
                    break
        self.assertGreater(found, 10, "did not find enough rows to check")

    def test_wide_glyph_rows_do_not_shift_the_divider(self):
        # A pack whose animation is two columns wide (moon, hearts, earth) must
        # not push the list/detail divider along.
        scr = spin.TextScreen(30, 100)
        st = spin.GalleryState(packs=spin.all_packs(), spinner="moon")
        spin.draw_gallery(scr, spin.Attrs(), st, 1.0)
        cols = set()
        for y, row in enumerate(scr.grid):
            if y < 3:
                continue
            line = self._cells(row)
            if "│" in line:
                cols.add(line.index("│"))
        self.assertEqual(len(cols), 1, "divider wandered to columns %s" % cols)

    def test_the_selected_row_is_marked(self):
        text = spin.render_gallery_text(width=100, height=30, idx=4)
        marked = [l for l in text.split("\n") if l.startswith(" ▌")]
        self.assertEqual(len(marked), 1)
        self.assertIn("Not in prod", marked[0])

    def test_the_preview_box_is_omitted_rather_than_drawn_empty(self):
        short = spin.render_gallery_text(width=100, height=11, query="pirate")
        self.assertNotIn("┌", short)
        tall = spin.render_gallery_text(width=100, height=24, query="pirate")
        self.assertIn("┌", tall)
        # nothing between the rules
        rules = [i for i, l in enumerate(tall.split("\n")) if "┌" in l or "└" in l]
        self.assertEqual(len(rules), 2)
        self.assertGreater(rules[1] - rules[0], 1)

    def test_the_preview_box_never_exceeds_the_real_spinner_width(self):
        for w in (62, 80, 100, 140, 200):
            text = spin.render_gallery_text(width=w, height=30)
            for line in text.split("\n"):
                if "┌" in line:
                    rule = line[line.index("┌"):line.index("┐") + 1]
                    self.assertLessEqual(spin.disp_width(rule),
                                         spin.SPINNER_MAX + 2, (w, rule))

    def test_the_detail_pane_disappears_on_a_narrow_terminal(self):
        self.assertNotIn("│", spin.render_gallery_text(width=58, height=20))
        self.assertIn("│", spin.render_gallery_text(width=100, height=20))

    def test_filters_are_reflected_in_the_header(self):
        text = spin.render_gallery_text(width=100, height=20, sfw=True,
                                        category="fiction")
        self.assertIn("sfw only", text)
        self.assertIn("in fiction", text)
        text = spin.render_gallery_text(width=100, height=20, spinner="moon",
                                        theme="matrix")
        self.assertIn("Moon", text)
        self.assertIn("Matrix", text)

    def test_an_empty_result_says_so(self):
        text = spin.render_gallery_text(width=100, height=20,
                                        query="zzzznotathing")
        self.assertIn("nothing matches", text)

    def test_wall_mode_lists_every_animation(self):
        text = spin.render_gallery_text(width=200, height=60, mode="wall")
        for sid, sp in spin.SPINNERS.items():
            self.assertIn(sp["name"], text, sid)

    def test_help_lists_every_key(self):
        text = spin.render_gallery_text(width=120, height=40, mode="help")
        for key, desc in spin.HELP_LINES:
            self.assertIn(desc, text)

    def test_the_hint_line_degrades_instead_of_being_chopped(self):
        for w in (200, 120, 100, 80, 62):
            line = spin.render_gallery_text(width=w, height=20).split("\n")[1]
            self.assertNotIn("…", line, "hints were chopped at width %d" % w)
            self.assertIn("q quit", line)


class TestPayloadAndDocs(unittest.TestCase):
    def test_packs_payload_is_json_serialisable_and_complete(self):
        payload = spin.packs_payload()
        json.dumps(payload)
        self.assertEqual(len(payload["packs"]), len(spin.PACKS))
        self.assertEqual(len(payload["spinners"]), len(spin.SPINNERS))
        self.assertEqual(len(payload["themes"]), len(spin.THEMES))
        for p in payload["packs"]:
            self.assertIn("id", p)
            self.assertIn("rating", p)

    def test_payload_respects_filters(self):
        self.assertTrue(all(p["rating"] == "sfw"
                            for p in spin.packs_payload(sfw=True)["packs"]))
        self.assertEqual(
            [p["id"] for p in spin.packs_payload(query="pirate")["packs"]],
            ["pirate"])

    def test_docs_tables_cover_the_registry(self):
        md = spin.docs_tables()
        for p in spin.PACKS:
            self.assertIn("`%s`" % p["id"], md)
        for sid in spin.SPINNERS:
            self.assertIn("`%s`" % sid, md)
        for tid in spin.THEMES:
            self.assertIn("`%s`" % tid, md)


class TestCLI(unittest.TestCase):
    """End-to-end, in a subprocess, with HOME pointed at a tempdir so a test
    run can never write to the developer's real Claude Code settings."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="spin-cli-home-")

    def run_spin(self, *args, **kw):
        env = dict(os.environ, HOME=self.home, NO_COLOR="1")
        env.pop("SPIN_NEWS_URL", None)
        return subprocess.run([sys.executable, SPIN_PY] + list(args),
                              capture_output=True, text=True, timeout=60,
                              env=env, **kw)

    def test_once_prints_a_single_verb(self):
        r = self.run_spin("--once")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(r.stdout.strip().split("\n")), 1)
        self.assertIn(r.stdout.strip(), spin.get_pack("profanity")["verbs"])

    def test_once_from_a_named_pack(self):
        r = self.run_spin("--once", "--pack", "zen")
        self.assertIn(r.stdout.strip(), spin.get_pack("zen")["verbs"])

    def test_list_json_parses(self):
        r = self.run_spin("--list", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(len(data["packs"]), len(spin.PACKS))

    def test_list_is_readable_and_within_a_sane_width(self):
        r = self.run_spin("--list")
        self.assertIn("packs (", r.stdout)
        self.assertIn("animations (", r.stdout)
        self.assertIn("themes (", r.stdout)
        for line in r.stdout.split("\n"):
            self.assertLessEqual(spin.disp_width(line), 100, repr(line))

    def test_no_colour_output_contains_no_escapes(self):
        r = self.run_spin("--list")
        self.assertNotIn("\033", r.stdout)

    def test_unknown_pack_fails_loudly(self):
        r = self.run_spin("--pack", "definitely-not-a-pack")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no pack", r.stdout)

    def test_status_set_and_restore_round_trip(self):
        settings = os.path.join(self.home, ".claude", "settings.json")
        os.makedirs(os.path.dirname(settings))
        original = {"theme": "dark",
                    "spinnerVerbs": {"mode": "append", "verbs": ["mine"]}}
        with open(settings, "w", encoding="utf-8") as f:
            json.dump(original, f)

        self.assertIn("unset", self.run_spin("--status").stdout)
        r = self.run_spin("--set", "pirate")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Pirate", r.stdout)
        self.assertIn("pirate", self.run_spin("--status").stdout)
        with open(settings, encoding="utf-8") as f:
            applied = json.load(f)
        self.assertEqual(applied["theme"], "dark")
        self.assertEqual(applied["spinnerVerbs"]["mode"], "replace")

        self.assertIn("restored", self.run_spin("--restore").stdout)
        with open(settings, encoding="utf-8") as f:
            self.assertEqual(json.load(f), original)

    def test_legacy_flags_still_work(self):
        self.assertEqual(self.run_spin("--set", "verbs").returncode, 0)
        self.assertIn("profanity", self.run_spin("--status").stdout)

    def test_export_import_round_trip(self):
        out = os.path.join(self.home, "zen.json")
        self.assertEqual(self.run_spin("--export", "zen", "-o", out).returncode, 0)
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["verbs"], spin.get_pack("zen")["verbs"])
        r = self.run_spin("--import", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(
            os.path.join(self.home, ".claude", "spinner-packs", "zen.json")))

    def test_a_custom_pack_appears_in_the_listing(self):
        d = os.path.join(self.home, ".claude", "spinner-packs")
        os.makedirs(d)
        with open(os.path.join(d, "mine.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "mine", "name": "Mine", "verbs": ["fiddling"]}, f)
        data = json.loads(self.run_spin("--list", "--json").stdout)
        self.assertIn("mine", [p["id"] for p in data["packs"]])

    def test_a_broken_custom_pack_does_not_stop_the_tool(self):
        d = os.path.join(self.home, ".claude", "spinner-packs")
        os.makedirs(d)
        with open(os.path.join(d, "broken.json"), "w", encoding="utf-8") as f:
            f.write("not json")
        r = self.run_spin("--list")
        self.assertEqual(r.returncode, 0)
        self.assertIn("broken.json", r.stdout)

    def test_piping_into_head_exits_cleanly(self):
        # `./spin.py --verbs | head` used to die with a BrokenPipeError
        # traceback the moment head closed the pipe.
        p1 = subprocess.Popen([sys.executable, SPIN_PY, "--verbs"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=dict(os.environ, HOME=self.home))
        p2 = subprocess.Popen(["head", "-c", "200"], stdin=p1.stdout,
                              stdout=subprocess.DEVNULL)
        p1.stdout.close()
        p2.wait(timeout=30)
        p1.wait(timeout=30)
        try:
            err = p1.stderr.read().decode()
        finally:
            p1.stderr.close()
        self.assertNotIn("Traceback", err)
        self.assertNotIn("BrokenPipeError", err)

    @unittest.skipIf(os.environ.get("SPIN_IN_SELFTEST"),
                     "already inside a --selftest run")
    def test_selftest_passes(self):
        r = self.run_spin("--selftest")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_help_mentions_the_headline_features(self):
        out = self.run_spin("--help").stdout
        for flag in ("--pack", "--spinner", "--theme", "--wall", "--restore",
                     "--list", "--mix", "--sfw", "--import", "--export"):
            self.assertIn(flag, out)

    def test_docs_table_generates_markdown(self):
        r = self.run_spin("--docs-table")
        self.assertEqual(r.returncode, 0)
        self.assertIn("| pack | verbs | rating |", r.stdout)


class TestWallRenderer(unittest.TestCase):
    def test_wall_paints_every_animation_and_stops(self):
        # --wall runs until Ctrl-C, so drive the renderer directly with a
        # duration and capture what it paints.
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            spin.wall(theme="rainbow", mode="none", duration=0.25)
        text = out.getvalue()
        self.assertNotIn("\033[38", text)         # mode="none" => no colour
        for sid, sp in spin.SPINNERS.items():
            self.assertIn(sp["name"], text, sid)

    def test_wall_restores_the_cursor_when_it_colours(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            spin.wall(theme="acid", mode="256", duration=0.2)
        text = out.getvalue()
        self.assertIn("\033[?25l", text)          # hidden
        self.assertTrue(text.endswith("\033[?25h"))  # and put back


if __name__ == "__main__":
    unittest.main()
