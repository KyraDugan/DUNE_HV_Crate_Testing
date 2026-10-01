"""
Utilizies for use within the WIEC Components Setup
"""

import time
import shutil
import sys
from colorama import Back, Fore, Style
FALLBACK_WIDTH = 70

#Colors assigned to each section title type
HEADER_TEXT = Fore.BLACK                      # header text is always black
HEADER_BACKGROUNDS = {                        # one background colour per kind of header
    "title":     Back.LIGHTWHITE_EX,          # program title at start-up
    "preflight": Back.LIGHTMAGENTA_EX,        # PREFLIGHT report
    "menu":      Back.LIGHTCYAN_EX,           # TEST ITEMS menu
    "item":      Back.LIGHTYELLOW_EX,         # "Running <item>"
    "section":   Back.LIGHTBLUE_EX,           # dashed prompt sections (WIB slots, test info)
}


def countdown_sleep(session, message, wait_time):
    """
    Creates a countdown to display in the log with an associated message and wait time.
    """
    wait_time = int(wait_time)
    session.log(f"{message}: waiting {wait_time} seconds\n")

    width = 30
    for remaining in range(wait_time, 0, -1):
        filled = int(width*(wait_time-remaining)/wait_time)
        bar = "#" * filled + "-" * (width - filled)
        print(f"\r{message} [{bar}] {remaining:3d}s left", end="", flush=True)

        time.sleep(1)
    print(f"\r{message} [{'#' * width}] {0:3d}s left")
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
