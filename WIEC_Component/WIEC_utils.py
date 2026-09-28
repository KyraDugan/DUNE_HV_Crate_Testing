"""
Utilizies for use within the WIEC Components Setup
"""

import time

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


def print_prompt_section(title):
    """
    Creates section title
    """
    print("\n" + "-" * 15 + f" {title} " + "-" * 15)