import unittest
from io import BytesIO

from PIL import Image

from tidyfin.badges import BadgeConfig, BadgeStyle, PRESETS, RenderBadge, compose_badges, render_text, sample_poster
from tidyfin.profiles import KINDS, LanguageProfile, PRESETS as LANG_PRESETS, preset_profile, presets_public, word_regex


class BadgeTests(unittest.TestCase):
    def test_render_text_placeholders_are_plain_replacements(self):
        self.assertEqual(render_text("UNTIL {date} ({days})", "01.11", 3), "UNTIL 01.11 (3)")
        self.assertEqual(render_text("{weird} {date}", "x", None), "{weird} x")   # no format() KeyError

    def test_style_validation(self):
        s = BadgeStyle.from_dict({"bg": "red", "size": 999, "bg_opacity": -3, "weight": "x", "border_width": 99, "preset": "nope"})
        self.assertEqual((s.bg, s.size, s.bg_opacity, s.weight, s.border_width, s.preset), ("#000000", 14.0, 0.0, "bold", 8, "glass"))
        self.assertEqual(BadgeStyle.from_dict({"bg": "#ff4967"}).bg, "#FF4967")

    def test_config_defaults_and_corner_validation(self):
        c = BadgeConfig.from_dict({"corner": "middle", "enabled": "yes", "text": "x" * 200}, BadgeConfig(text="d", corner="top-left"))
        self.assertEqual((c.corner, c.enabled, len(c.text)), ("top-left", True, 60))   # invalid values fall back to defaults

    def test_presets_are_valid_styles(self):
        for name in PRESETS:
            self.assertEqual(BadgeStyle.from_preset(name).preset, name)

    def test_compose_all_corners_keeps_size_and_changes_pixels(self):
        src = sample_poster(400)
        corners = ("top-left", "top-right", "bottom-left", "bottom-right", "top-center", "bottom-center")
        out = compose_badges(src, [RenderBadge(f"B{i}", c, 0, BadgeStyle()) for i, c in enumerate(corners)])
        self.assertEqual(Image.open(BytesIO(out)).size, Image.open(BytesIO(src)).size)
        self.assertNotEqual(out, src)

    def test_stacking_uses_order_and_empty_text_is_skipped(self):
        src = sample_poster(400)
        a = compose_badges(src, [RenderBadge("ONE", "top-left", 0, BadgeStyle()), RenderBadge("TWO", "top-left", 1, BadgeStyle())])
        b = compose_badges(src, [RenderBadge("TWO", "top-left", 1, BadgeStyle()), RenderBadge("ONE", "top-left", 0, BadgeStyle())])
        self.assertEqual(a, b)                      # input order does not matter, only `order`
        self.assertEqual(compose_badges(src, [RenderBadge("", "top-left", 0, BadgeStyle())]),
                         compose_badges(src, []))

    def test_very_long_text_is_shrunk_to_fit(self):
        out = compose_badges(sample_poster(300), [RenderBadge("A VERY LONG BADGE TEXT THAT CANNOT FIT", "top-center", 0, BadgeStyle())])
        self.assertTrue(out.startswith(b"\xff\xd8"))


class ProfileTests(unittest.TestCase):
    def test_all_presets_load_with_every_kind_and_unique_codes(self):
        codes = [p["code"] for p in LANG_PRESETS]
        self.assertEqual(len(codes), len(set(codes)))
        for p in presets_public():
            self.assertEqual(set(p["badges"]), set(KINDS))
            self.assertTrue(p["codes"])

    def test_polish_preset_keeps_legacy_tags_and_texts(self):
        pl = preset_profile("pol")
        self.assertEqual(pl.tag_map()["voiceover"], ["lektor-pl"])
        self.assertEqual(pl.tag_map()["dismiss"], ["brak-pl"])
        self.assertEqual([pl.badges[k].text for k in ("voiceover", "dub", "subs", "audio")],
                         ["LEKTOR PL", "DUBBING PL", "NAPISY PL", "POLSKI"])

    def test_generic_profile_gets_generated_tags_and_texts(self):
        p = LanguageProfile.from_dict({"code": "Swe", "name": "Swedish", "native": "Svenska", "short": "SV"})
        self.assertEqual(p.code, "swe")
        self.assertEqual(p.tag_map()["subs"], ["subs-sv"])
        self.assertEqual((p.badges["audio"].text, p.badges["dub"].text), ("SVENSKA", "SV DUB"))

    def test_word_lists_are_normalised(self):
        p = LanguageProfile.from_dict({"code": "x", "codes": "A, b ,A,,", "hints": [" Foo ", "foo"]})
        self.assertEqual((p.codes, p.hints), (["a", "b"], ["foo"]))

    def test_word_regex_is_whole_word_and_unicode_aware(self):
        r = word_regex(["pl", "дубляж"])
        self.assertTrue(r.search("Film.PL.1080p") and r.search("Фильм [Дубляж]"))
        self.assertFalse(r.search("Apple.Plot") or r.search("simple"))
        self.assertIsNone(word_regex([]))


if __name__ == "__main__":
    unittest.main()
