"""Operator pop-up windows, showing the pages of the qualification protocol.

The protocol deck lives at GUI/ISEG_Qualification_Protocol.pdf.  Its pages are
exported once to GUI/protocol_pngs/<page>.png

    pdftoppm -png -r 150 GUI/ISEG_Qualification_Protocol.pdf GUI/protocol_pngs/page

and a Step just names the page number it wants.  The window itself is
GUI/pop_window.show_image_popup -- the same function the FEMB checkout uses --
so an operator moving between the two test stands sees the same thing.

Nothing in here may raise.  A pop-up failure that escaped would abort the flow,
and an abort in this test powers the crate off: a missing PNG, a run over ssh
with no DISPLAY, or a machine without python3-tk has to degrade to a printed
line and let the terminal prompts carry the test as they always did.
"""

import os
import sys

DEFAULT_DIR = os.path.join("GUI", "protocol_pngs")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def image_path(page, directory=None):
    """A page number (or a file name) -> the PNG to display."""
    directory = directory or DEFAULT_DIR
    name = f"{page}.png" if str(page).isdigit() else str(page)
    if not os.path.isabs(directory):
        directory = os.path.join(REPO_ROOT, directory)
    return os.path.join(directory, name)


def available(cfg=None):
    """Can a window actually be opened here?  Returns (ok, reason)."""
    if cfg is not None and not cfg.get("iseg_popups_enabled", True):
        return False, "turned off by iseg_popups_enabled"

    # tkinter raises only when Tk() is called, which would be in the middle of
    # the flow.  Checking the environment first keeps the failure at start-up.
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return False, "no DISPLAY -- there is no desktop to open a window on"

    try:
        import tkinter                      # noqa: F401
        from PIL import Image, ImageTk      # noqa: F401
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    return True, "ready"


def make_popup(cfg=None, log=print):
    """Returns popup(title, page), or None when windows cannot be shown.

    Called once at start-up so the operator is told up front that the pictures
    are not coming, rather than finding out at step 16.
    """
    ok, reason = available(cfg)
    if not ok:
        log(f"Pop-up windows are off ({reason}) -- the instructions stay in the terminal.")
        return None

    directory = (cfg or {}).get("iseg_popup_dir") or DEFAULT_DIR

    def popup(title, page):
        path = image_path(page, directory)
        if not os.path.isfile(path):
            print(f"    (pop-up image not found: {path})")
            return
        try:
            from GUI import pop_window
            pop_window.show_image_popup(title=title, image_path=path)
        except Exception as exc:
            # KeyboardInterrupt is deliberately not caught: Ctrl-C during a
            # pop-up must still abort the test and power the crate down.
            print(f"    (pop-up failed: {type(exc).__name__}: {exc})")

    log(f"Pop-up windows on, showing {os.path.join(directory, '<page>.png')}")
    return popup
