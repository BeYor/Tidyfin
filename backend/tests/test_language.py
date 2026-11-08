import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tidyfin.language import LanguageDetector, Matcher, NO_KINDS, badge_kind, upgrade_kind

K = frozenset
from tidyfin.profiles import preset_profile
from tidyfin.settings import LanguageSettings
from tidyfin.store import Store
from tests.helpers import FakeJellyfin


def M(code):
    return Matcher(preset_profile(code))


class PolishSignalTests(unittest.TestCase):
    """Behaviour of the original script, now expressed through the Polish preset."""

    def setUp(self):
        self.pl = M("pol")

    def test_filename_signals(self):
        self.assertEqual(self.pl.from_filename("Film.2020.[PL-Dubbing]"), (K({"dub"}), False, []))
        self.assertEqual(self.pl.from_filename("Film [Lektor PL]"), (K({"voiceover"}), False, []))
        self.assertEqual(self.pl.from_filename("Film [Napisy PL]"), (NO_KINDS, True, []))
        self.assertEqual(self.pl.from_filename("Film.PL.1080p"), (NO_KINDS, False, ["Film.PL.1080p"]))   # ambiguous
        self.assertEqual(self.pl.from_filename("Film.English"), (NO_KINDS, False, []))
        self.assertEqual(self.pl.from_filename("Apple.Plot"), (NO_KINDS, False, []))

    def test_badge_kind(self):
        self.assertEqual([badge_kind(K({"voiceover"}), False), badge_kind(K({"dub"}), False), badge_kind(K({"audio"}), False)],
                         ["voiceover", "dub", "audio"])
        self.assertEqual((badge_kind(NO_KINDS, True), badge_kind(NO_KINDS, False)), ("subs", None))

    def test_two_or_more_findings_show_the_general_language_badge(self):
        """Voice-over + subtitles, dub + subtitles, voice-over + dub, all three - and plain audio + subtitles."""
        self.assertEqual(badge_kind(K({"voiceover"}), True), "audio")
        self.assertEqual(badge_kind(K({"dub"}), True), "audio")
        self.assertEqual(badge_kind(K({"voiceover", "dub"}), False), "audio")
        self.assertEqual(badge_kind(K({"voiceover", "dub"}), True), "audio")
        self.assertEqual(badge_kind(K({"audio"}), True), "audio")

    def test_upgrade_kind_collects_findings(self):
        self.assertEqual(upgrade_kind(K({"voiceover"}), "dub"), K({"voiceover", "dub"}))
        self.assertEqual(upgrade_kind(K({"audio"}), "dub"), K({"dub"}))        # plain audio gives way
        self.assertEqual(upgrade_kind(None, "audio"), K({"audio"}))
        self.assertEqual(upgrade_kind(K({"dub"}), None), K({"dub"}))
        self.assertEqual(upgrade_kind(NO_KINDS, NO_KINDS), NO_KINDS)

    def test_streams(self):
        streams = [{"Type": "Audio", "Language": "eng", "Title": "English"},
                   {"Type": "Audio", "Language": "und", "Title": "Lektor PL"},
                   {"Type": "Subtitle", "Language": "pol", "Title": ""}]
        self.assertEqual(self.pl.from_streams(streams), (K({"voiceover"}), True, []))
        self.assertEqual(self.pl.from_streams([{"codec_type": "audio", "tags": {"language": "pol"}}]), (K({"audio"}), False, []))
        self.assertEqual(self.pl.from_streams([{"Type": "Audio", "Language": "und", "Title": "PL"}]), (NO_KINDS, False, ["PL"]))

    def test_voiceover_and_dub_in_one_release_are_both_detected(self):
        streams = [{"Type": "Audio", "Language": "pol", "Title": "Lektor PL"}, {"Type": "Audio", "Language": "pol", "Title": "Dubbing PL"}]
        self.assertEqual(self.pl.from_streams(streams), (K({"voiceover", "dub"}), False, []))
        self.assertEqual(self.pl.from_filename("Film.2020.Lektor.Dubbing.PL"), (K({"voiceover", "dub"}), False, []))

    def test_manual_tags_and_dismiss(self):
        self.assertEqual(self.pl.from_tags(["Lektor-PL", "napisy-pl"]), (K({"voiceover"}), True, False))
        self.assertEqual(self.pl.from_tags(["Audio-PL"]), (K({"audio"}), False, False))
        self.assertEqual(self.pl.from_tags(["Brak-PL", "Lektor-PL"]), (NO_KINDS, False, True))
        self.assertEqual(self.pl.from_tags(None), (NO_KINDS, False, False))


class OtherLanguageTests(unittest.TestCase):
    def test_french_release_words(self):
        fr = M("fre")
        self.assertEqual(fr.from_filename("Film.2020.VF.1080p")[0], K({"dub"}))
        self.assertEqual(fr.from_filename("Film.2020.VOSTFR.1080p"), (NO_KINDS, True, []))

    def test_short_codes_are_not_filename_hints_so_titles_do_not_false_positive(self):
        it = M("ita")
        self.assertEqual(it.from_filename("It.2017.1080p.BluRay"), (NO_KINDS, False, []))     # the movie "It"
        self.assertEqual(it.from_filename("Film.ITA.1080p"), (NO_KINDS, False, ["Film.ITA.1080p"]))
        self.assertEqual(M("ger").from_filename("Es.2017.1080p"), (NO_KINDS, False, []))

    def test_confirmed_by_stream_language_code_even_with_short_code(self):
        self.assertEqual(M("ita").from_streams([{"Type": "Audio", "Language": "it"}]), (K({"audio"}), False, []))

    def test_custom_tags_follow_the_short_code(self):
        self.assertEqual(M("ger").from_tags(["Dub-DE", "subs-de"]), (K({"dub"}), True, False))
        self.assertEqual(M("ger").from_tags(["no-de"]), (NO_KINDS, False, True))

    def test_short_dub_word_needs_a_whole_word_match_in_stream_titles(self):
        pl = M("pol")
        self.assertEqual(pl.classify_title("PL Dub"), "dub")
        self.assertEqual(pl.classify_title("Dubbing"), "dub")
        self.assertEqual(pl.classify_title("Dublin Audio"), "audio")   # "dub" inside another word


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.jf = FakeJellyfin()
        self.cfg = LanguageSettings(deep_scan=False, profiles=[preset_profile("pol"), preset_profile("ger")])

    def det(self, store=None):
        return LanguageDetector(self.jf, self.cfg, store)

    def test_manual_tags_settle_a_movie_like_a_series(self):
        item = {"Tags": ["napisy-pl"], "Path": "/m/x [Lektor PL].mkv", "MediaStreams": [
            {"Type": "Audio", "Language": "pol", "Title": "Lektor"}]}
        found = {m.profile.code: (k, s) for m, k, s, _ in self.det().analyze_item(item, "movie")}
        self.assertEqual(found["pol"], (NO_KINDS, True))              # only what the admin wrote, not the detected lektor

    def test_explain_item_tells_what_was_found_and_where(self):
        item = {"Tags": [], "Path": "/m/Film [Dubbing PL].mkv", "MediaStreams": [
            {"Type": "Audio", "Language": "pol", "Title": "Lektor"}, {"Type": "Subtitle", "Language": "pol"},
            {"Type": "Audio", "Language": "ger", "Title": "Deutsch"}]}
        rows = {r["code"]: r for r in self.det().explain_item(item, "movie")}
        pol = rows["pol"]
        self.assertEqual((pol["audio"], pol["subs"], pol["badge"], pol["text"]), (["voiceover", "dub"], True, "audio", "POLSKI"))
        self.assertEqual(pol["sources"], ["streams", "filename"])
        self.assertEqual(rows["ger"]["badge"], "audio")

    def test_explain_item_reports_tags_dismissal_and_ambiguity(self):
        tagged = self.det().explain_item({"Tags": ["lektor-pl"], "Path": "/m/x.mkv", "MediaStreams": []}, "movie")[0]
        self.assertEqual((tagged["audio"], tagged["sources"], tagged["badge"]), (["voiceover"], ["tags"], "voiceover"))
        gone = self.det().explain_item({"Tags": ["brak-pl"], "Path": "/m/x.mkv", "MediaStreams": []}, "movie")[0]
        self.assertEqual((gone["dismissed"], gone["badge"], gone["sources"]), (True, None, []))
        vague = self.det().explain_item({"Tags": [], "Path": "/m/Film.PL.1080p.mkv", "MediaStreams": []}, "movie")[0]
        self.assertEqual((vague["badge"], vague["uncertain"]), (None, ["Film.PL.1080p"]))

    def test_one_item_can_carry_two_languages(self):
        item = {"Tags": [], "Path": "/m/x.mkv", "MediaStreams": [
            {"Type": "Audio", "Language": "pol", "Title": "Lektor"}, {"Type": "Audio", "Language": "ger", "Title": "Deutsch"}]}
        found = {m.profile.code: (k, s) for m, k, s, _ in self.det().analyze_item(item, "movie")}
        self.assertEqual(found, {"pol": (K({"voiceover"}), False), "ger": (K({"audio"}), False)})

    def test_dismiss_tag_affects_only_its_own_profile(self):
        item = {"Tags": ["Brak-PL"], "Path": "/m/x [Lektor].mkv", "MediaStreams": [{"Type": "Audio", "Language": "ger"}]}
        found = {m.profile.code: (k, s) for m, k, s, _ in self.det().analyze_item(item, "movie")}
        self.assertEqual(found, {"pol": (NO_KINDS, False), "ger": (K({"audio"}), False)})

    def test_file_name_layer(self):
        item = {"Tags": [], "MediaStreams": [], "Path": "/m/Film (2020) [PL-Dubbing].mkv"}
        self.assertEqual(self.det().analyze_item(item, "movie")[0][1:3], (K({"dub"}), False))

    def test_disabled_profiles_are_ignored(self):
        self.cfg.profiles[1].enabled = False
        self.assertEqual([m.profile.code for m in self.det().matchers], ["pol"])

    def test_series_tag_skips_episode_scan(self):
        calls = []
        self.cfg.profiles = [preset_profile("pol")]          # with a second profile the episodes WOULD be scanned
        self.jf.series_episodes = lambda sid: calls.append(sid) or []
        out = self.det().analyze_item({"Id": "s1", "Tags": ["Lektor-PL"]}, "series")
        self.assertEqual(out[0][1], K({"voiceover"}))
        self.assertEqual(calls, [])

    def test_series_scans_limited_episodes_and_stops_early(self):
        self.cfg.max_episodes = 2
        self.jf.episodes["s1"] = [
            {"MediaStreams": [], "Path": "/s/e1.mkv"},
            {"MediaStreams": [{"Type": "Audio", "Language": "pol", "Title": "Lektor"}, {"Type": "Subtitle", "Language": "pol"}], "Path": "/s/e2.mkv"},
            {"MediaStreams": [], "Path": "/s/e3 [Dubbing PL].mkv"}]
        pl = [r for r in self.det().analyze_item({"Id": "s1", "Tags": []}, "series") if r[0].profile.code == "pol"][0]
        self.assertEqual(pl[1:3], (K({"voiceover"}), True))

    def test_series_collects_voiceover_and_dub_from_different_episodes(self):
        self.cfg.max_episodes = 3
        self.jf.episodes["s1"] = [
            {"MediaStreams": [{"Type": "Audio", "Language": "pol", "Title": "Lektor"}, {"Type": "Subtitle", "Language": "pol"}], "Path": "/s/e1.mkv"},
            {"MediaStreams": [], "Path": "/s/e2 [Dubbing PL].mkv"}]
        pl = [r for r in self.det().analyze_item({"Id": "s1", "Tags": []}, "series") if r[0].profile.code == "pol"][0]
        self.assertEqual(pl[1:3], (K({"voiceover", "dub"}), True))
        self.assertEqual(badge_kind(pl[1], pl[2]), "audio")

    def test_deep_scan_finds_subtitle_files_and_probes_each_file_once(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Film.mkv").write_bytes(b"x")
            (Path(d) / "Film.pl.srt").write_text("1")
            (Path(d) / "Film.de.srt").write_text("1")
            self.cfg.deep_scan = True
            item = {"Tags": [], "MediaStreams": [], "Path": str(Path(d) / "Film.mkv")}
            with mock.patch("tidyfin.language.subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stdout='{"streams": []}')
                det = self.det()
                found = {m.profile.code: s for m, k, s, _ in det.analyze_item(item, "movie")}
            self.assertEqual(found, {"pol": True, "ger": True})   # "de" is fine for subtitle FILES
            self.assertEqual(run.call_count, 1)                    # ffprobe shared by both profiles

    def test_path_prefix_mapping(self):
        self.cfg.jellyfin_path_prefix, self.cfg.local_path_prefix = "/data", "/media"
        self.assertEqual(self.det().map_jellyfin_path_to_local("/data/movies/a.mkv"), "/media/movies/a.mkv")
        self.assertIsNone(self.det().map_jellyfin_path_to_local(None))

    def test_compute_labels_uncertain_cache_and_fallback(self):
        store = Store(":memory:")
        self.jf.add_movie(1, "m1", "Polish", path="/m/Polish [Lektor PL].mkv")
        self.jf.add_movie(2, "m2", "English", path="/m/Eng.mkv")
        self.jf.add_movie(3, "m3", "Unsure", path="/m/Film.PL.mkv")
        result, uncertain = self.det(store).compute()
        self.assertEqual(result["movie_1"]["labels"], [{"profile": "pol", "kind": "voiceover"}])
        self.assertNotIn("movie_2", result)
        self.assertEqual([(u["title"], u["profile"]) for u in uncertain], [("Unsure", "pol")])
        self.assertEqual(store.load_library_id_cache()["movie_1"], "m1")
        del self.jf.movies["1"]                                  # drops out of the bulk list...
        self.assertEqual(self.det(store).compute()[0]["movie_1"]["jellyfin_id"], "m1")   # ...but the cache finds it

    def test_no_active_profiles_means_no_work(self):
        self.cfg.profiles = []
        self.assertEqual(self.det().compute(), ({}, []))


if __name__ == "__main__":
    unittest.main()
