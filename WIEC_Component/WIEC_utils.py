"""
Utilizies for use within the WIEC Components Setup
"""

import time
import shutil
import sys
from colorama import Back, Fore, Style
FALLBACK_WIDTH = 70

#Colors assigned to each section title type
HEADER_TEXT = Fore.WHITE                      # header text is always white
HEADER_BACKGROUNDS = {                        # one background colour per kind of header
    "title":     Back.LIGHTWHITE_EX,          # program title at start-up
    "preflight": Back.LIGHTMAGENTA_EX,        # PREFLIGHT report
    "menu":      Back.LIGHTCYAN_EX,           # TEST ITEMS menu
    "item":      Back.LIGHTYELLOW_EX,         # "Running <item>"
    "section":   Back.LIGHTBLUE_EX,           # dashed prompt sections (WIB slots, test info)
}



BAR_WIDTH = 30     # characters in a progress bar


def draw_bar(message, elapsed, total):
    """
    One frame of a progress bar, redrawn on the current line: message [#####-----]  Ns left
    Past `total` the bar stays full at 0 s left until the caller finishes it.
    """
    total = max(total, 1)
    filled = min(BAR_WIDTH, int(BAR_WIDTH * elapsed / total))
    remaining = max(0, int(total - elapsed))
    print(f"\r{message} [{'#' * filled}{'-' * (BAR_WIDTH - filled)}] {remaining:3d}s left", end="", flush=True)


def finish_bar(message):
    """
    Draws the bar full and ends the line, so the next output starts on a new line.
    """
    print(f"\r{message} [{'#' * BAR_WIDTH}] {0:3d}s left")


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



#def print_prompt_section(title):
    #"""
    #Creates section title
    #"""
   # print("\n" + "-" * 15 + f" {title} " + "-" * 15)


def terminal_width():
    """
    Current width of the terminal window in characters.  
    Read every time so a header printed is resized automatically
    """
    return shutil.get_terminal_size((FALLBACK_WIDTH, 24)).columns


def rule(char="="):
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


def print_banner(title, kind):
    """
    Full-width banner: a line of =, the title centred in black on the background colour
    for this kind of header (HEADER_BACKGROUNDS), a line of =.
    """
    bar = title.center(terminal_width())
    print(f"\n{color(bar, HEADER_TEXT + HEADER_BACKGROUNDS[kind])}\n")


def print_prompt_section(title):
    """
    Section title centred in a full-width line of dashes, black on the "section" background.
    """
    bar = f" {title} ".center(terminal_width(), "-")
    print("\n" + color(bar, HEADER_TEXT + HEADER_BACKGROUNDS["section"]))
