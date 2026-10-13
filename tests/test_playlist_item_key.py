"""Spotify's Feb 2026 API renamed playlist entry `track` -> `item`.
Both shapes must yield a track; the fields filter must request both."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from spt_tui.spotify_client import SpotifyClient

T = {"id": "t1", "uri": "spotify:track:t1", "name": "Song"}

def test_new_shape():
    assert SpotifyClient.playlist_entry_track({"added_at": "x", "item": T}) == T

def test_legacy_shape():
    assert SpotifyClient.playlist_entry_track({"added_at": "x", "track": T}) == T

def test_missing_or_null():
    assert SpotifyClient.playlist_entry_track({"item": None, "track": None}) == {}
    assert SpotifyClient.playlist_entry_track({}) == {}
    assert SpotifyClient.playlist_entry_track(None) == {}

if __name__ == "__main__":
    test_new_shape(); test_legacy_shape(); test_missing_or_null(); print("ok")
