"""
Utilities for WIEC Component testing
Most of these utilities are for the terminal GUI
"""

import time
import shutil
import sys
from colorama import Back, Fore, Style


#--------------RGB COLORS FOR TERMINAL GUI----------------------------------------------------------------------

def _hex_to_rgb(hex_color):
    """
    "#rrggbb" -> (red, green, blue) as numbers 0-255.
    """
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))

def _rgb_code(hex_color, layer):
    """
    Terminal code for an exact colour given as "#rrggbb". layer 38 = text, 48 = background.
    """
    red, green, blue = _hex_to_rgb(hex_color)
    return f"\x1b[{layer};2;{red};{green};{blue}m"


def text_rgb(hex_color):
    """
    Text in an exact colour from hex
    """
    return _rgb_code(hex_color, 38)


def background_rgb(hex_color):
    """
    Background in an exact colour from hex
    """
    return _rgb_code(hex_color, 48)


# Each colour has one job, so it tells the operator something.
BANNER_BACKGROUND_HEX = "#0a5cff"
BANNER_TEXT_HEX = "#fffff2"
HEADER_HEX = "#15c9ed"
PASS_HEX = "#0dd649"
FAIL_HEX = "#db441a"
ACTION_HEX = "#b930e3"
BAR_START_HEX = "#83f5e5"
BAR_END_HEX = "#e761bd"
BAR_EMPTY_HEX = "#e6e6e6"   # the not-yet-filled part of a progress bar (light grey track)


HEADER = text_rgb(HEADER_HEX) + Style.BRIGHT
BANNER_STYLE = text_rgb(BANNER_TEXT_HEX) + background_rgb(BANNER_BACKGROUND_HEX) + Style.BRIGHT 
ACTION = text_rgb(ACTION_HEX) + Style.BRIGHT   
PASS_COLOR = text_rgb(PASS_HEX) + Style.BRIGHT
FAIL_COLOR = text_rgb(FAIL_HEX) + Style.BRIGHT
HEAVY_RULE = "═"                  
LIGHT_RULE = "─"         


#--------------TERMINAL GUI----------------------------------------------------------------------
FALLBACK_WIDTH = 70

def terminal_width():
    """
    Current width of the terminal window in characters.  
    Read every time so a header printed is resized automatically
    """
    return shutil.get_terminal_size((FALLBACK_WIDTH, 24)).columns


def rule(char=HEAVY_RULE):
    """
    A line of `char` exactly as wide as the terminal window.
    """
    return char * terminal_width()


def color(text, code):
    """
    Text in the given color, but only when printing to the terminal, not a file.
    """
    if not sys.stdout.isatty():
        return text
    return f"{code}{text}{Style.RESET_ALL}"


def print_banner(title, kind=None):
    """
    A big moment (program title, menu, an item starting): the title centred in bold white
    on a full-width navy bar, with a blank line above and below.
    `kind` is no longer used; it is kept so the existing calls still work.
    """
    bar = title.center(terminal_width())
    print(f"\n{color(bar, BANNER_STYLE)}\n")


def print_prompt_section(title):
    """
    A section inside an item:  ── Title ──────────  (bold blue title, line to the window's edge).
    """
    head = f"{LIGHT_RULE * 2} {title} "
    tail = LIGHT_RULE * max(0, terminal_width() - len(head))
    print("\n" + color(head, HEADER) + color(tail, HEADER) + "\n")


#--------------ASKS FOR INPUT----------------------------------------------------------------------

def print_action(text):
    """
    Something the operator has to do, marked so it stands out:  ▶ text  (bold purple).
    """
    print(color(f"▶ {text}", ACTION))


def ask(text, prompt="> "):
    """
    Shows the operator an instruction (▶ text, bold purple) and returns what they type,
    with surrounding spaces removed. Use it for every question or "press Enter" in the test.
    """
    print_action(text)
    return input(color(f"{prompt}", ACTION)).strip()


def verdict(passed):
    """
    "PASS" in green or "FAIL" in red, for result lines.
    """
    return color("PASS", PASS_COLOR) if passed else color("FAIL", FAIL_COLOR)


#--------------PROGRESS BAR----------------------------------------------------------------------
BAR_MIN_WIDTH = 10     # a progress bar never gets narrower than this, even in a small window

def blend(start_hex, end_hex, fraction):
    """
    The colour `fraction` of the way from start_hex to end_hex (0 = start, 1 = end), as "#rrggbb".
    """
    start, end = _hex_to_rgb(start_hex), _hex_to_rgb(end_hex)
    mixed = [round(s + (e - s) * fraction) for s, e in zip(start, end)]
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def bar_width(message, suffix):
    """
    How many cells the bar gets so that   message <bar> suffix   fills the window without
    wrapping onto a second row (a wrapped line breaks the \\r redraw). At least BAR_MIN_WIDTH.
    """
    return max(BAR_MIN_WIDTH, terminal_width() - len(message) - len(suffix) - 3)


def solid_bar(filled, width):
    """
    A progress bar `width` cells wide, as solid colour blocks: `filled` cells coloured by their
    position along the bar (BAR_START_HEX at the left, blending to BAR_END_HEX at the right),
    then the rest in the grey BAR_EMPTY_HEX track. Each cell is a space with a coloured
    background. Not a terminal (e.g. a log file): plain #####----- instead.
    """
    if not sys.stdout.isatty():
        return "#" * filled + "-" * (width - filled)
    steps = max(width - 1, 1)
    cells = [background_rgb(blend(BAR_START_HEX, BAR_END_HEX, i / steps)) + " " for i in range(filled)]
    track = background_rgb(BAR_EMPTY_HEX) + " " * (width - filled)
    return "".join(cells) + track + Style.RESET_ALL


def draw_bar(message, elapsed, total):
    """
    One frame of a progress bar, redrawn on the current line: message <bar> Ns left,
    as wide as the window allows. Past `total` the bar stays full at 0 s left.
    """
    remaining = max(0, int(total - elapsed))
    suffix = f"{remaining:3d}s left"
    width = bar_width(message, suffix)
    filled = min(width, int(width * elapsed / max(total, 1)))
    print(f"\r{message} {solid_bar(filled, width)} {suffix}", end="", flush=True)


def finish_bar(message):
    """
    Draws the bar full and ends the line, so the next output starts on a new line.
    """
    suffix = f"{0:3d}s left"
    width = bar_width(message, suffix)
    print(f"\r{message} {solid_bar(width, width)} {suffix}")


def countdown(message, seconds):
    """
    Waits `seconds` with a progress bar. Needs no session, so any file can use it.
    """
    seconds = int(seconds)
    for elapsed in range(seconds):
        draw_bar(message, elapsed, seconds)
        time.sleep(1)
    finish_bar(message)


def countdown_sleep(session, message, wait_time):
    """
    countdown(), with the start and end written to the session log.
    """
    session.log(f"{message}: waiting {int(wait_time)} seconds\n")
    countdown(message, wait_time)
    session.log(f"{message}: wait complete")


