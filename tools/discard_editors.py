"""Dev tool: close every open editor WITHOUT saving (resets Fakturama after an aborted run).

Close All (Ctrl+Shift+W) asks "Save Parts" with every dirty editor ticked; Space unticks the
selected entry (verified visually — the list exposes no check state to UIA), then OK closes.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from i2c.ui.app import Fakturama  # noqa: E402

app = Fakturama(Path("runs/dev"))
app.restore()  # undo any maximized editor stack from an aborted run
dirty = [t.window_text() for t in app.win.descendants(control_type="TabItem") if t.window_text().startswith("*")]
if dirty:
    app.activate_tab(dirty[0])  # the shortcut only acts when an editor (not a list view) has focus
app.key("^+w")
time.sleep(1)
while True:
    dlgs = [w for w in app.win.wrapper_object().children()
            if w.element_info.control_type == "Window" and w.window_text() == "Save Parts"]
    if not dlgs:
        break
    for item in dlgs[0].descendants(control_type="ListItem"):
        print("discarding", item.window_text())
        app.focus()
        item.click_input()
        item.type_keys("{SPACE}")
    app.press("OK", dlgs[0])
    time.sleep(1)
print("open tabs:", [t.window_text() for t in app.win.descendants(control_type="TabItem")])
