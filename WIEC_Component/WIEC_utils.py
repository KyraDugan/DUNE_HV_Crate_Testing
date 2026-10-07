"""
Utilities for WIEC Component testing
Most of these utilities are for the terminal GUI
"""

import time
import shutil
import sys
import colorsys
import random
from colorama import Style
from . import output_log


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
IN_PALLATE = "#D0A11A"
BANNER_BACKGROUND_HEX = "#43397F"
BANNER_TEXT_HEX = "#FCFCF0"
HEADER_HEX = "#93629A"
PASS_HEX = "#69995D"
FAIL_HEX = "#6F1A07"
ACTION_HEX = "#1A7997" 
BAR_EMPTY_HEX = "#C2BDA2"   # the not-yet-filled part of a progress bar


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
    empty_bar = terminal_width() * " "
    bar = title.center(terminal_width())
    print()
    print(color(empty_bar, BANNER_STYLE))
    print(color(bar, BANNER_STYLE))
    print(color(empty_bar, BANNER_STYLE))
    print()


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
    response = input(prompt).strip()
    output_log.record_answer(response)
    return response     # plain prompt: colour codes in input() confuse backspace


def verdict(passed):
    """
    "PASS" in green or "FAIL" in red, for result lines.
    """
    return color("PASS", PASS_COLOR) if passed else color("FAIL", FAIL_COLOR)


def confirm_input_ask(text, reenter_text = "", retry_text = "", prompt="> "):
    """
    Asks the user to reenter their input to confirm.
    """

    while True:
        print_action(text)
        first_input = input(prompt).strip()
        output_log.record_answer(first_input)
        print_action(reenter_text or f"Please reenter to confirm:")
        second_input = input(prompt).strip()
        output_log.record_answer(second_input)
        if first_input == second_input:
            return first_input
        else:
            print(color(retry_text or "Inputs do not match. Please try again.", FAIL_COLOR))
        


#--------------PROGRESS BAR WITH RANDOM PRETTY GRADIENT--------------------------------------------

BAR_MIN_WIDTH = 10     # a progress bar never gets narrower than this, even in a small window

# Random progress-bar gradients. Both ends share one lightness and saturation (the "same row
# of the colour picker"); only the hue differs, by a moderate random amount.
BAR_LIGHTNESS = 0.58          # 0-1: mid lightness reads well on a white terminal
BAR_SATURATION = 0.72         # 0-1: clearly coloured, not greyish next to the grey track
BAR_HUE_GAP = (60, 150)       # degrees between the two ends: clearly different, but not opposites
BAR_AVOID_HUES = []           # hue ranges a gradient may not pass through, e.g. [(66, 95)]; empty = any hue

def _hsl_to_hex(hue, saturation=BAR_SATURATION, lightness=BAR_LIGHTNESS):
    """
    A colour given by hue (degrees, any number: it wraps at 360), saturation and lightness (0-1),
    as "#rrggbb".
    """
    red, green, blue = colorsys.hls_to_rgb((hue % 360) / 360, lightness, saturation)
    return "#{:02x}{:02x}{:02x}".format(round(red * 255), round(green * 255), round(blue * 255))


def _hue_step(start_hue, end_hue):
    """
    The shortest way round the colour wheel from start_hue to end_hue, in degrees:
    positive = clockwise, negative = anticlockwise. E.g. 350 -> 20 is +30, not -330.
    """
    return ((end_hue - start_hue + 180) % 360) - 180


def blend_hue(start_hue, end_hue, fraction):
    """
    The colour `fraction` of the way from start_hue to end_hue (0 = start, 1 = end), going the
    short way round the colour wheel at fixed lightness and saturation, as "#rrggbb".
    Unlike mixing red/green/blue, this never passes through grey or brown.
    """
    return _hsl_to_hex(start_hue + _hue_step(start_hue, end_hue) * fraction)


def _gradient_allowed(start_hue, end_hue):
    """
    True if the gradient from start_hue to end_hue stays out of every BAR_AVOID_HUES range,
    checked at both ends and three points in between.
    """
    step = _hue_step(start_hue, end_hue)
    for fraction in (0, 0.25, 0.5, 0.75, 1):
        hue = (start_hue + step * fraction) % 360
        if any(low <= hue <= high for low, high in BAR_AVOID_HUES):
            return False
    return True


def random_bar_hues():
    """
    A random (start hue, end hue) pair for a progress bar: BAR_HUE_GAP degrees apart, either
    way round, and not passing through BAR_AVOID_HUES. Draws again until a pair fits.
    """
    while True:
        start = random.uniform(0, 360)
        end = (start + random.choice([-1, 1]) * random.uniform(*BAR_HUE_GAP)) % 360
        if _gradient_allowed(start, end):
            return start, end


_bar_hues = {}   # message -> hues of the bar being drawn now, so its colours stay fixed while it runs


def _hues_for(message):
    """
    The colours of the bar with this message: chosen at its first frame, then reused.
    """
    if message not in _bar_hues:
        _bar_hues[message] = random_bar_hues()
    return _bar_hues[message]


def bar_width(message, suffix):
    """
    How many cells the bar gets so that   message <bar> suffix   fills the window without
    wrapping onto a second row (a wrapped line breaks the \\r redraw). At least BAR_MIN_WIDTH.
    """
    return max(BAR_MIN_WIDTH, terminal_width() - len(message) - len(suffix) - 3)


def solid_bar(filled, width, hues):
    """
    A progress bar `width` cells wide, as solid colour blocks: `filled` cells shading from the
    start hue to the end hue in `hues` (from random_bar_hues), then the rest in the grey
    BAR_EMPTY_HEX track. Each cell is a space with a coloured background.
    Not a terminal (e.g. a log file): plain #####----- instead.
    """
    if not sys.stdout.isatty():
        return "#" * filled + "-" * (width - filled)
    start_hue, end_hue = hues
    steps = max(width - 1, 1)
    cells = [background_rgb(blend_hue(start_hue, end_hue, i / steps)) + " " for i in range(filled)]
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
    print(f"\r{message} {solid_bar(filled, width, _hues_for(message))} {suffix}", end="", flush=True)


def finish_bar(message):
    """
    Draws the bar full and ends the line, so the next output starts on a new line.
    Then forgets this bar's colours, so the next bar gets new ones.
    """
    suffix = f"{0:3d}s left"
    width = bar_width(message, suffix)
    print(f"\r{message} {solid_bar(width, width, _hues_for(message))} {suffix}")
    _bar_hues.pop(message, None)


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


