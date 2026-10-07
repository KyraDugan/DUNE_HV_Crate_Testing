"""
Session log for the WIEC component test.

Copies everything printed during a run into a text file in the run's results folder.
Importantly, the file is plain text.

-------------------------------------------------------------------------------------------------------
config.json keys used:

"WIEC_results_folder"                                Folder for the new style of WIEC results

-------------------------------------------------------------------------------------------------------
"""

import contextlib
import re
import sys
import time
import os

from datetime import datetime
from pathlib import Path
from typing import Optional, TextIO

COLOUR_CODE = re.compile(r"\x1b\[[0-9;]*m")   # terminal colour codes, e.g. \x1b[38;2;34;73;201m

_log_file = None
_original_stdout = None
_original_stderr = None



def _strip_colour(text):
    """
    text with every terminal color code removed, and Windows-style line ends (\r\n) made plain (\n).
    Improves readability significantly for log.
    """
    return COLOUR_CODE.sub("", text).replace("\r\n", "\n")



class Tee:
    """
    Stands in for sys.stdout (or sys.stderr): everything written goes to the terminal unchanged,
    and a cleaned copy goes to the log file.
    """

    def __init__(self, terminal_stream, log_file):
        self.terminal_stream = terminal_stream
        self.log_file = log_file
        self._line = ""      # the line being built for the file; a \r throws it away

    def write(self, text):
        self.terminal_stream.write(text)
        self.terminal_stream.flush()

        for character in _strip_colour(text):
            if character == "\r":            # a progress bar redrawing: forget the old frame
                self._line = ""
            elif character == "\n":          # line finished: now it goes in the file
                self.log_file.write(self._line + "\n")
                self._line = ""
            else:
                self._line += character
        self.log_file.flush()
        return len(text)

    def flush(self):
        self.terminal_stream.flush()
        self.log_file.flush()

    def isatty(self):
        return self.terminal_stream.isatty()

    def finish(self):
        """
        Writes out a last line that never got its newline (e.g. the run stopped mid-bar).
        """
        if self._line:
            self.log_file.write(self._line + "\n")
            self._line = ""

    def add_answer(self, answer):
        """
        Finishes the current line (the "> " prompt) with what the operator typed, which
        input() reads from the keyboard without passing it through here.
        """
        self.log_file.write(self._line + _strip_colour(answer) + "\n")
        self._line = ""
        self.log_file.flush()


def start(session):
    """
    Starts copying everything printed into <run folder>/<wiec_session_log_name>.
    Call it after record_results.run_path(session). Returns the log file's path.
    """
    global _log_file, _original_stdout, _original_stderr

    if _log_file is not None:        # already running: don't wrap the Tee in another Tee
        return _log_file.name

    log_path = os.path.join(session.wiec_run_dir, session.json_data["wiec_session_log_name"])
    _log_file = open(log_path, "w", encoding="utf-8")

    _original_stdout = sys.stdout
    _original_stderr = sys.stderr
    sys.stdout = Tee(_original_stdout, _log_file)
    sys.stderr = Tee(_original_stderr, _log_file)

    session.datastore["session_log"] = log_path
    session.log(f"Session log: {log_path}")
    return log_path


def stop(session=None):
    """
    Stops the log: the terminal streams go back to normal and the file is closed.
    Safe to call when no log is running.
    """
    global _log_file

    if _log_file is None:
        return

    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, Tee):
            stream.finish()

    sys.stdout = _original_stdout
    sys.stderr = _original_stderr
    _log_file.close()
    _log_file = None

def is_active():
    """
    True while a session log is running.
    """
    return _log_file is not None


def log(message=""):
    """
    Writes message to the log file only (never the terminal). Does nothing if no log is running.
    """
    if _log_file is None:
        return
    for line in _strip_colour(str(message)).split("\n"):
        _log_file.write(line.split("\r")[-1] + "\n")
    _log_file.flush()


class LogOnly:
    def __init__(self, log_stream: TextIO):
        self.log_stream = log_stream

    def write(self, data: str) -> int:
        self.log_stream.write(data)
        self.log_stream.flush()
        return len(data)

    def flush(self) -> None:
        self.log_stream.flush()
        


def record_answer(answer):
    """
    Puts the operator's typed answer into the log after its prompt. Does nothing if no log is running.
    """
    if isinstance(sys.stdout, Tee):
        sys.stdout.add_answer(answer)


@contextlib.contextmanager
def terminal_suppressed():
    if _log_file is None:
        yield
        return

    with contextlib.redirect_stdout(LogOnly(_log_file)):
        yield
