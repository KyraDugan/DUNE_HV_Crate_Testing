"""
Utilities for WIEC Component testing
Most of these utilities are for the terminal GUI
"""

import time
import shutil
import sys
import random
import threading
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
IN_PALETTE = "#D0A11A"
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


def confirm_input_ask(text, reenter_text = "", retry_text = "", no_input_text = "", prompt="> "):
    """
    Asks the user to reenter their input to confirm.
    """

    while True:
        print_action(text)
        first_input = input(prompt).strip()
        while not first_input:
            print(color(no_input_text or f"You must input a value.", FAIL_COLOR))
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
        

#--------------PASS/FAIL COLOR TEXT FORMATTING--------------------------------------------


def verdict(passed):
    """
    "PASS" in green or "FAIL" in red, for result lines.
    """
    return color("PASS", PASS_COLOR) if passed else color("FAIL", FAIL_COLOR)


def fail_text(text):
    """
    A failure message in the FAIL color
    """
    return color(text, FAIL_COLOR)


def pass_text(text):
    """
    A success message in the PASS color
    """
    return color(text, PASS_COLOR)

#--------------PROGRESS BAR WITH RANDOM PRETTY GRADIENT--------------------------------------------

BAR_MIN_WIDTH = 10     # a progress bar never gets narrower than this, even in a small window

# Random progress-bar gradients made from the palette above. Each bar picks two different colours
# from BAR_COLORS and blends from the first to the second, so every bar matches the scheme.
# FAIL_HEX is left out on purpose: a rust bar would look like a failure.

BAR_COLORS = [IN_PALETTE, BANNER_BACKGROUND_HEX, HEADER_HEX, PASS_HEX, ACTION_HEX]


def blend_hex(start_hex, end_hex, fraction):
    """
    The colour `fraction` of the way from start_hex to end_hex (0 = start, 1 = end), as "#rrggbb",
    by mixing their red, green and blue values.
    """
    start = _hex_to_rgb(start_hex)
    end = _hex_to_rgb(end_hex)
    return "#{:02x}{:02x}{:02x}".format(*(round(s + (e - s) * fraction) for s, e in zip(start, end)))


def random_bar_colors():
    """
    A random (start colour, end colour) pair for a progress bar: two different colours from
    BAR_COLORS, in random order, so every pair can appear in both directions.
    """
    start, end = random.sample(BAR_COLORS, 2)
    return start, end


_bar_colors = {}   # message -> colours of the bar being drawn now, so they stay fixed while it runs


def _colors_for(message):
    """
    The colours of the bar with this message: chosen at its first frame, then reused.
    """
    if message not in _bar_colors:
        print("\n")
        _bar_colors[message] = random_bar_colors()
    return _bar_colors[message]


def bar_width(message, suffix):
    """
    How many cells the bar gets so that   message <bar> suffix   fills the window without
    wrapping onto a second row (a wrapped line breaks the \\r redraw). At least BAR_MIN_WIDTH.
    """
    return max(BAR_MIN_WIDTH, terminal_width() - len(message) - len(suffix) - 3)


def solid_bar(filled, width, colors):
    """
    A progress bar `width` cells wide, as solid colour blocks: `filled` cells shading from the
    start colour to the end colour in `colors` (from random_bar_colors), then the rest in the
    BAR_EMPTY_HEX track. Each cell is a space with a coloured background.
    Not a terminal (e.g. a log file): plain #####----- instead.
    """
    if not sys.stdout.isatty():
        return "#" * filled + "-" * (width - filled)
    start_color, end_color = colors
    steps = max(width - 1, 1)
    cells = [background_rgb(blend_hex(start_color, end_color, i / steps)) + " " for i in range(filled)]
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
    print(f"\r{message} {solid_bar(filled, width, _colors_for(message))} {suffix}", end="", flush=True)


def finish_bar(message):
    """
    Draws the bar full and ends the line, so the next output starts on a new line.
    Then forgets this bar's colours, so the next bar gets new ones.
    """
    suffix = f"{0:3d}s left"
    width = bar_width(message, suffix)
    print(f"\r{message} {solid_bar(width, width, _colors_for(message))} {suffix}\n")
    _bar_colors.pop(message, None)

def run_with_bar(message, expected_s, function, *args, **kwargs):
    """
    Runs function(*args, **kwargs) while a progress bar fills over expected_s seconds. For work
    that blocks (an instrument query, a long command), so it can't draw a bar itself.
    Returns whatever function returns. If function raises an error, the same error is raised
    here, after the bar's line is finished.
    """
    outcome = {}    # the background thread puts the result (or the error) here

    def worker():
        try:
            outcome["result"] = function(*args, **kwargs)
        except BaseException as error:
            outcome["error"] = error

    thread = threading.Thread(target=worker, daemon=True)
    start = time.time()
    thread.start()

    try:
        while thread.is_alive():
            draw_bar(message, time.time() - start, expected_s)
            thread.join(timeout=0.25)
    except KeyboardInterrupt:
        # Never leave an instrument call running while the shutdown starts using the same
        # instruments: let the current call finish first, then pass the Ctrl-C on.
        print()
        _bar_colors.pop(message, None)
        print(f"Stopping after the current step ({message}) finishes...")
        thread.join()
        raise
    finish_bar(message)

    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("result")


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


