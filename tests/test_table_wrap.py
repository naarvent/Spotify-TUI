"""Up on the first row jumps to the last; Down on the last row jumps to the first."""
import os, sys, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from textual.app import App
from spt_tui.widgets import WrapTable

R = []
def check(n, c): R.append(bool(c)); print("PASS" if c else "FAIL", "-", n)

class A(App):
    def compose(self):
        t = WrapTable(id="t"); t.cursor_type = "row"; t.add_column("a")
        for i in range(5): t.add_row(str(i))
        yield t
        e = WrapTable(id="e"); e.add_column("a"); yield e

async def main():
    app = A()
    async with app.run_test() as pilot:
        t = app.query_one("#t"); t.focus(); await pilot.pause()
        await pilot.press("up"); await pilot.pause()
        check("up en la primera -> ultima", t.cursor_row == 4)
        await pilot.press("down"); await pilot.pause()
        check("down en la ultima -> primera", t.cursor_row == 0)
        await pilot.press("down"); await pilot.pause()
        check("down normal", t.cursor_row == 1)
        await pilot.press("up"); await pilot.pause()
        check("up normal", t.cursor_row == 0)
        e = app.query_one("#e"); e.focus(); await pilot.press("up", "down"); await pilot.pause()
        check("tabla vacia no falla", True)
asyncio.run(main())
print(f"{sum(R)}/{len(R)} checks passed")
sys.exit(0 if all(R) else 1)
