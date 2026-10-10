"""Dev tool: print the UIA tree of the active Fakturama editor (or a dialog).

    python tools/dump_uia.py                 # active editor tab
    python tools/dump_uia.py "Select the address"   # a dialog or editor tab whose name contains this
"""

import sys

from pywinauto import Desktop


def walk(el, depth=0):
    ei = el.element_info
    r = ei.rectangle
    val = ""
    try:
        val = f" val={el.iface_value.CurrentValue!r}"
    except Exception:
        pass
    try:
        val += f" toggle={el.get_toggle_state()}"
    except Exception:
        pass
    print("  " * depth + f"{ei.control_type} {ei.name!r} ({r.left},{r.top} {r.width()}x{r.height()}){val}")
    for c in el.children():
        walk(c, depth + 1)


win = Desktop(backend="uia").window(class_name="SWT_Window0", title_re=r"Fakturama.*").wrapper_object()
if len(sys.argv) > 1:
    target = next(e for e in win.descendants() if e.element_info.control_type in ("Window", "Tab")
                  and sys.argv[1] in e.element_info.name)
else:
    # editor area = the Tab whose TabItem is selected and is not a view (Documents etc. live lower)
    tabs = [t for t in win.descendants(control_type="Tab") if t.rectangle().top < 200]
    target = tabs[0] if tabs else win
walk(target)
