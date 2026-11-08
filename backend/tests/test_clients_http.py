import unittest
from datetime import timezone

from tidyfin.clients import ArrClient, JellyfinClient
from tidyfin.errors import AuthError, UpstreamError
from tests.helpers import ArrState, JellyfinState, VALID_KEY, arr_server, http_server, jellyfin_server


class JellyfinClientTests(unittest.TestCase):
    def test_public_info_ping_and_bad_key(self):
        with http_server(jellyfin_server(JellyfinState())) as srv:
            self.assertEqual(JellyfinClient(srv.url).public_info()["ServerName"], "Test")
            self.assertEqual(JellyfinClient(srv.url, VALID_KEY).ping()["version"], "10.9.0")
            with self.assertRaises(UpstreamError) as ctx:
                JellyfinClient(srv.url, "wrong").ping()
            self.assertEqual(ctx.exception.code, "bad_api_key")

    def test_login_and_idempotent_api_key_creation(self):
        state = JellyfinState()
        with http_server(jellyfin_server(state)) as srv:
            jf = JellyfinClient(srv.url)
            auth = jf.authenticate_by_name("admin", "pass")
            self.assertTrue(auth["is_admin"])
            with self.assertRaises(AuthError):
                jf.authenticate_by_name("admin", "nope")
            keys = {jf.ensure_api_key(auth["access_token"]), jf.ensure_api_key(auth["access_token"])}
            self.assertEqual((keys, len(state.keys)), ({"generated-key"}, 1))

    def test_path_annotation_is_used_when_provider_ids_are_missing(self):
        state = JellyfinState()
        state.movies = [{"Id": "a", "ProviderIds": {"Tmdb": "111"}, "Path": "/m/A/A.mkv"},
                        {"Id": "b", "ProviderIds": {}, "Path": "/m/B [tmdbid-222]/B.mkv"},
                        {"Id": "c", "ProviderIds": {}, "Path": "/m/C [tmdbid=333]/C.mkv"},
                        {"Id": "d", "ProviderIds": {}, "Path": "/m/D/D.mkv"}]
        with http_server(jellyfin_server(state)) as srv:
            jf = JellyfinClient(srv.url, VALID_KEY)
            self.assertEqual([jf.resolve_item(i, None, "movie")[0]["Id"] for i in (111, 222, 333)], ["a", "b", "c"])
            self.assertEqual(jf.resolve_item(999, None, "movie"), (None, True))
            self.assertEqual(jf.lookup_by_key("movie_222"), "b")

    def test_failure_to_check_is_reported_not_treated_as_missing(self):
        with http_server(jellyfin_server(JellyfinState())) as srv:
            jf = JellyfinClient(srv.url, "wrong")
            self.assertEqual(jf.resolve_item(1, None, "movie"), (None, False))
            self.assertEqual(jf.last_played_across_users("a"), (None, False))

    def test_latest_play_across_users(self):
        state = JellyfinState()
        state.last_played = {("a", "u1"): "2026-01-10T10:00:00Z", ("a", "u2"): "2026-03-05T08:00:00Z"}
        with http_server(jellyfin_server(state)) as srv:
            jf = JellyfinClient(srv.url, VALID_KEY)
            last, ok = jf.last_played_across_users("a")
            self.assertEqual((ok, (last.year, last.month, last.day), last.tzinfo), (True, (2026, 3, 5), timezone.utc))
            self.assertEqual(jf.last_played_across_users("none"), (None, True))

    def test_collections(self):
        state = JellyfinState()
        with http_server(jellyfin_server(state)) as srv:
            jf = JellyfinClient(srv.url, VALID_KEY)
            self.assertEqual(jf.find_collection_by_name("LEAVING SOON - movies"), "box1")
            self.assertEqual(jf.create_collection("X"), "new-collection")
            self.assertEqual(jf.add_to_collection("box1", ["i1", "i2"]).status_code, 204)
            jf.remove_from_collection("box1", ["i3"])
            self.assertEqual([c[0] for c in state.collection_calls], ["add", "remove"])


class ArrClientTests(unittest.TestCase):
    def test_ping_tags_and_wrong_key(self):
        with http_server(arr_server(ArrState())) as srv:
            self.assertEqual(ArrClient("radarr", srv.url, "k").ping()["version"], "5.2.0")
            self.assertEqual(ArrClient("radarr", srv.url, "k").tags(), {1: "keep"})
            with self.assertRaises(UpstreamError):
                ArrClient("radarr", srv.url, "bad").tags()

    def test_ensure_tag_reuses_case_insensitively_or_creates(self):
        state = ArrState()
        with http_server(arr_server(state)) as srv:
            c = ArrClient("radarr", srv.url, "k")
            self.assertEqual(c.ensure_tag("KEEP"), 1)
            new_id = c.ensure_tag("Do-Not-Delete")
            self.assertEqual((new_id, state.calls[-1]), (2, ("create_tag", {"label": "do-not-delete"})))
            self.assertEqual(c.ensure_tag("do-not-delete"), 2)       # now exists

    def test_edit_tags_through_the_bulk_editor(self):
        state = ArrState()
        with http_server(arr_server(state, "radarr")) as srv:
            c = ArrClient("radarr", srv.url, "k")
            c.edit_tags(7, [1], "add")
            self.assertEqual((state.items[7]["tags"], state.calls[-1][0]), ([1], "editor"))
            self.assertEqual(state.calls[-1][1], {"movieIds": [7], "tags": [1], "applyTags": "add"})
            c.edit_tags(7, [1], "remove")
            self.assertEqual(state.items[7]["tags"], [])

    def test_sonarr_uses_series_ids(self):
        state = ArrState()
        with http_server(arr_server(state, "sonarr")) as srv:
            ArrClient("sonarr", srv.url, "k").edit_tags(7, [1], "add")
            self.assertIn("seriesIds", state.calls[-1][1])

    def test_falls_back_to_read_modify_write_when_the_editor_is_missing(self):
        state = ArrState(editor=False)
        state.items[7]["tags"] = [5]
        with http_server(arr_server(state)) as srv:
            ArrClient("radarr", srv.url, "k").edit_tags(7, [1], "add")
            self.assertEqual((state.calls[-1][0], sorted(state.items[7]["tags"])), ("put_item", [1, 5]))

    def test_network_errors_become_upstream_errors(self):
        import requests
        arr = ArrClient("radarr", "http://127.0.0.1:1", "k", session=requests.Session())   # plain session: no retry delays
        with self.assertRaises(UpstreamError):
            arr.items()
        self.assertTrue(arr.configured)
        self.assertFalse(ArrClient("sonarr", "", "").configured)


if __name__ == "__main__":
    unittest.main()


class JellyfinActivityTests(unittest.TestCase):
    """Watched series, existing ids, other users' activity and tag writes (HTTP level)."""

    def test_series_play_date_comes_from_episodes(self):
        state = JellyfinState()
        state.episode_plays = {("s1", "u2", "IsPlayed"): "2026-10-01T10:00:00.0000000Z",
                               ("s1", "u1", "IsResumable"): "2026-10-20T10:00:00Z"}
        with http_server(jellyfin_server(state)) as srv:
            jf = JellyfinClient(srv.url, VALID_KEY)
            last, ok = jf.last_played_across_users("s1", "series")
            self.assertTrue(ok)
            self.assertEqual((last.month, last.day), (10, 20))          # a half-watched episode counts too
            self.assertEqual(jf.last_played_across_users("s1"), (None, True))   # the series row alone has no play date
            self.assertEqual(jf.last_played_across_users("other", "series"), (None, True))

    def test_failed_episode_lookup_is_unknown_not_unwatched(self):
        state = JellyfinState()
        state.episodes_fail = True
        with http_server(jellyfin_server(state)) as srv:
            self.assertEqual(JellyfinClient(srv.url, VALID_KEY).last_played_across_users("s1", "series"), (None, False))

    def test_existing_item_ids(self):
        state = JellyfinState()
        state.items_by_id = {"a": {"Id": "a"}, "b": {"Id": "b"}}
        with http_server(jellyfin_server(state)) as srv:
            jf = JellyfinClient(srv.url, VALID_KEY)
            self.assertEqual(jf.existing_item_ids(["a", "gone", "b", ""]), {"a", "b"})
            self.assertEqual(jf.existing_item_ids([]), set())
            self.assertIsNone(JellyfinClient(srv.url, "wrong").existing_item_ids(["a"]))

    def test_resume_items_map_episodes_to_their_series(self):
        state = JellyfinState()
        state.resume = {"u1": [{"Id": "e1", "Type": "Episode", "SeriesId": "s1", "UserData": {"LastPlayedDate": "2026-10-25T10:00:00Z"}},
                               {"Id": "m1", "Type": "Movie", "UserData": {}}]}
        with http_server(jellyfin_server(state)) as srv:
            rows = JellyfinClient(srv.url, VALID_KEY).resume_items("u1")
            self.assertEqual([r["item_id"] for r in rows], ["s1", "m1"])
            self.assertEqual(rows[0]["last_played"].day, 25)
            self.assertIsNone(rows[1]["last_played"])
            self.assertIsNone(JellyfinClient(srv.url, "wrong").resume_items("u1"))

    def test_set_item_tags_adds_and_removes(self):
        state = JellyfinState()
        state.items_by_id = {"a": {"Id": "a", "Name": "X", "Tags": ["keep", "Lektor-PL"]}}
        with http_server(jellyfin_server(state)) as srv:
            self.assertTrue(JellyfinClient(srv.url, VALID_KEY).set_item_tags("a", add=["napisy-pl"], remove=["lektor-pl"]))
            self.assertEqual(state.item_updates[0][1]["Tags"], ["keep", "napisy-pl"])
            self.assertFalse(JellyfinClient(srv.url, VALID_KEY).set_item_tags("missing", add=["x"]))
