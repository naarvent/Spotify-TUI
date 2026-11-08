"""Create Playlist only asks what the provider supports: Spotify = public + collaborative + description;
YouTube Music = description; Subsonic = name only."""
import os, sys, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from textual.widgets import Input
from spt_tui.app import SptPy
from spt_tui.providers.base import Capabilities

R = []
def check(n, c, d=""): R.append(bool(c)); print("PASS" if c else "FAIL", "-", n, d if not c else "")

class Fake:
    def __init__(self, caps): self.capabilities = caps; self.created = []
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None, "total": 0}
    def get_playback(self): return {}
    def recently_played(self, limit=50): return {"items": []}
    def ensure(self): return self
    def fmt_duration(self, ms): return "0:00"
    def create_playlist(self, name, public=True, collaborative=False, description=""):
        self.created.append((name, public, collaborative, description)); return {"name": name, "id": "x"}

class TApp(SptPy):
    def __init__(self, caps):
        super().__init__(); self.spotify = Fake(caps)

async def submit(app, pilot, attr, value):
    w = getattr(app, attr); w.value = value
    app.post_message(Input.Submitted(w, value)); await pilot.pause(); await pilot.pause()

async def run(label, caps, expected_inputs, expected_call):
    app = TApp(caps)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.section_idx = app.section_order.index("lib")
        names = [(getattr(c, "data", {}) or {}).get("name") for c in app.lib_list.children]
        app.lib_list.index = names.index("Create Playlist")
        app.action_prompt_create_playlist(); await pilot.pause()
        await submit(app, pilot, "create_name_input", "Mi lista")
        seen = []
        for attr in ("create_public_input", "create_collab_input", "create_desc_input"):
            if getattr(app, attr, None) is not None:
                seen.append(attr)
                await submit(app, pilot, attr, "y" if attr != "create_desc_input" else "desc")
        await pilot.pause(); await asyncio.sleep(0.3)
        check(f"{label}: prompts {expected_inputs}", seen == expected_inputs, repr(seen))
        check(f"{label}: create_playlist called {expected_call}", app.spotify.created == [expected_call], repr(app.spotify.created))

async def main():
    await run("spotify", Capabilities(), ["create_public_input", "create_collab_input", "create_desc_input"],
              ("Mi lista", True, True, "desc"))
    await run("ytmusic", Capabilities(playlist_visibility=False), ["create_desc_input"],
              ("Mi lista", False, False, "desc"))
    await run("subsonic", Capabilities(playlist_visibility=False, playlist_description=False), [],
              ("Mi lista", False, False, ""))
asyncio.run(main())
print(f"{sum(R)}/{len(R)} checks passed"); sys.exit(0 if all(R) else 1)
