"""Operator-guided step flow: the three step kinds, and the engine that runs them.

Every step in an operator-guided test is one of three kinds:

    AUTO    software does it, reads it and judges it
    USER    the operator has to do something, then confirms
    CHECK   the operator looks at the hardware and answers PASS / FAIL

A flow is a *list* of Steps, not a function full of if/else, which is what lets
the repeated parts (the three safety-jumper configurations) be generated
instead of copy-pasted, and what lets a GUI reuse the flow by replacing
Console with a class of its own.

Failures come in two severities:

    safe    the module is unpowered or at low voltage -- record it, warn, ask
            whether to carry on, and keep the overall verdict at FAIL
    unsafe  anything under high voltage -- ramp down, power off, abort

Nothing here knows what an MPOD is; the flow supplies the actions.
"""

import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

AUTO = "AUTO"
USER = "USER"
CHECK = "CHECK"

SAFE = "safe"
UNSAFE = "unsafe"

WIDTH = 64


class AbortFlow(RuntimeError):
    """Raised to stop the flow: an unsafe failure, or the operator quitting."""

    def __init__(self, message, unsafe=False):
        super().__init__(message)
        self.unsafe = unsafe


# ----------------------------------------------------------------------
# Steps
# ----------------------------------------------------------------------
@dataclass
class Step:
    kind: str
    title: str
    prompt: str = ""
    # AUTO only: action(ctx) -> True / False / (bool, detail) / None for "no verdict"
    action: Optional[Callable] = None
    # CHECK only: what the operator should be seeing if the module is good
    expect: str = ""
    severity: str = SAFE
    key: str = ""
    # USER only: prompt for a typed value and store it in ctx.answers[input_key]
    input_key: str = ""
    # USER + input_key: is an empty answer allowed (meaning "use the default")?
    allow_empty: bool = False
    # TBD steps are recorded but never counted in the verdict
    informational: bool = False
    # USER / CHECK only: the protocol page to show in a pop-up window first.
    # A page number, or a file name; the Console resolves it.  AUTO steps do
    # not get one -- a full-screen window would cover the live progress line.
    image: str = ""

    def result_key(self):
        return self.key or self.title.lower().replace(" ", "_")


@dataclass
class StepResult:
    key: str
    kind: str
    title: str
    passed: Optional[bool]
    detail: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def verdict(self):
        if self.passed is None:
            return "N/A"
        return "PASS" if self.passed else "FAIL"


# ----------------------------------------------------------------------
# Terminal front end
# ----------------------------------------------------------------------
class Console:
    """Terminal prompts.  A GUI subclasses this and overrides the four asks.

    `popup(title, page)` is optional: when it is there, a step that names a
    protocol page gets a window showing it before the terminal asks anything.
    The window is only ever a picture -- every verdict is still typed here, so
    there is one place that decides PASS/FAIL whether the pictures work or not.
    """

    def __init__(self, popup=None):
        self._popup = popup

    def _box(self, heading, body, char="═"):
        top = "╔" + char * (WIDTH - 2) + "╗"
        sep = "╠" + char * (WIDTH - 2) + "╣"
        bottom = "╚" + char * (WIDTH - 2) + "╝"

        print()
        print(top)
        print("║" + heading.center(WIDTH - 2) + "║")
        print(sep)
        for raw_line in body.splitlines() or [""]:
            wrapped = textwrap.wrap(raw_line, WIDTH - 4) or [""]
            for line in wrapped:
                print("║  " + line.ljust(WIDTH - 4) + "║")
        print(bottom)

    # -- output ------------------------------------------------------
    def info(self, message):
        print(f"    {message}")

    def step_header(self, index, total, step):
        print()
        print(f"[{index}/{total}] [{step.kind}] {step.title}")

    def result(self, step_result):
        mark = {"PASS": "PASS", "FAIL": "FAIL", "N/A": "----"}[step_result.verdict()]
        detail = f"  {step_result.detail}" if step_result.detail else ""
        print(f"    -> {mark}{detail}")

    def warn(self, heading, body):
        self._box(heading, body, char="!")

    def header(self, heading, body):
        self._box(heading, body)

    def show_page(self, title, page):
        """Show one protocol page.  Used for the pages that are not steps."""
        if self._popup is None or not page:
            return
        self._popup(title, page)

    def show_image(self, step):
        """The step's own picture, shown before it asks the operator anything.

        AUTO steps are skipped even if one is set: the window is full screen
        and blocks until it is closed, which during the ramp would hide the
        very progress line the operator is meant to be watching.
        """
        if not step.image or step.kind == AUTO:
            return
        self.show_page(step.title, step.image)

    # -- input -------------------------------------------------------
    def ask_continue(self, title, prompt):
        """A USER step needs a typed confirmation, never a bare Enter.

        The operator has just been asked to do something physical.  Pressing
        Enter is the reflex that gets used to skip past prompts, so the answer
        has to be an actual word: 'y' to confirm the work is done, 'q' to stop
        the test if something is wrong.
        """
        self._box("USER ACTION REQUIRED", prompt)
        while True:
            answer = input(
                "              Done?  type y to confirm, q to stop: "
            ).strip().lower()
            if answer in ("y", "yes", "done", "ok"):
                return True
            if answer in ("q", "quit", "stop", "abort"):
                raise AbortFlow(f"operator stopped the test at: {title}")
            if not answer:
                print("              Pressing Enter is not enough -- "
                      "type y once you have actually done it.")
            else:
                print("              Please type y (done) or q (stop the test).")

    def ask_check(self, title, prompt, expect):
        body = prompt
        if expect:
            body += f"\n\nPASS means: {expect}"
        self._box("OPERATOR CHECK", body)
        while True:
            answer = input("              PASS / FAIL (q to stop): ").strip().lower()
            if answer in ("p", "pass", "y", "yes", "ok"):
                return True
            if answer in ("f", "fail", "n", "no"):
                return False
            if answer in ("q", "quit", "stop", "abort"):
                raise AbortFlow(f"operator stopped the test at: {title}")
            print("              Please answer PASS or FAIL.")

    def ask_text(self, prompt, allow_empty=False, key=""):
        self._box("USER ACTION REQUIRED", prompt)
        hint = "type the value" + (" (Enter for the default)" if allow_empty else "")
        while True:
            answer = input(f"              {hint}, or q to stop: ").strip()
            if answer.lower() in ("q", "quit", "stop", "abort"):
                raise AbortFlow("operator stopped the test at a data entry step")
            if answer or allow_empty:
                return answer
            print("              Please type a value.")

    def ask_yes_no(self, question, default=False):
        """No default on a bare Enter: this is only ever asked after a failure."""
        suffix = "[y/n]"
        while True:
            answer = input(f"    {question} {suffix}: ").strip().lower()
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            print("    Please answer y or n.")


class AutoConsole(Console):
    """Answers its own prompts, for --dry-run walkthroughs of the flow text."""

    def __init__(self, serial_number="DRYRUN123", answers=None):
        super().__init__(popup=None)
        self.serial_number = serial_number
        self.answers = dict(answers or {})
        self.answers.setdefault("expected_serial", serial_number)

    def show_page(self, title, page):
        """Named, not opened: a dry run must not put 12 windows on the screen."""
        print(f"              [dry run] pop-up: page {page}  ({title})")

    def ask_continue(self, title, prompt):
        self._box("USER ACTION REQUIRED", prompt)
        print("              [dry run] y")
        return True

    def ask_check(self, title, prompt, expect):
        body = prompt + (f"\n\nPASS means: {expect}" if expect else "")
        self._box("OPERATOR CHECK", body)
        print("              [dry run] PASS")
        return True

    def ask_text(self, prompt, allow_empty=False, key=""):
        """Answers per prompt.  Returning the serial number for every typed
        field is what made the dry run feed 'DRYRUN123' to the DAQ channel."""
        answer = self.answers.get(key, self.serial_number)
        self._box("USER ACTION REQUIRED", prompt)
        print(f"              [dry run] {answer}")
        return answer

    def ask_yes_no(self, question, default=False):
        print(f"    {question} [dry run] {'yes' if default else 'no'}")
        return default


# ----------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------
class FlowRunner:
    """Walks a list of Steps and collects a StepResult for each one."""

    def __init__(self, console, ctx, on_unsafe=None, log=None):
        self.console = console
        self.ctx = ctx
        # Called before the flow unwinds on an unsafe failure: ramp down, power off
        self.on_unsafe = on_unsafe or (lambda: None)
        self.log = log or (lambda message: None)
        self.results: List[StepResult] = []
        self.aborted = False
        self.abort_reason = ""

    def _unique_key(self, key):
        """Repeated steps (four power cycles) must not share a result key."""
        taken = {r.key for r in self.results}
        if key not in taken:
            return key
        index = 2
        while f"{key}_{index}" in taken:
            index += 1
        return f"{key}_{index}"

    def record(self, step, passed, detail=""):
        step_result = StepResult(
            key=self._unique_key(step.result_key()), kind=step.kind, title=step.title,
            passed=passed, detail=detail,
        )
        self.results.append(step_result)
        self.console.result(step_result)
        self.log(f"{step.title}: {step_result.verdict()} {detail}".rstrip())
        return step_result

    def run(self, steps: List[Step]):
        total = len(steps)
        for index, step in enumerate(steps, start=1):
            self.console.step_header(index, total, step)
            self.console.show_image(step)
            try:
                passed, detail = self._run_step(step)
            except AbortFlow as exc:
                # The operator typed q.  Record where the test stopped.
                self.aborted = True
                if not self.abort_reason:
                    self.abort_reason = str(exc)
                self.record(step, False, str(exc))
                raise
            except Exception as exc:
                # An exception inside an unsafe step is a trip by another name
                self.record(step, False, f"{type(exc).__name__}: {exc}")
                self._handle_failure(step, f"{type(exc).__name__}: {exc}")
                continue

            if step.informational:
                self.record(step, None, detail)
                continue

            self.record(step, passed, detail)
            if not passed:
                self._handle_failure(step, detail)

        return self.results

    def _run_step(self, step):
        if step.kind == USER:
            if step.input_key:
                answer = self.console.ask_text(
                    step.prompt, allow_empty=step.allow_empty, key=step.input_key)
                self.ctx.answers[step.input_key] = answer
                return True, f"entered {answer!r}"
            self.console.ask_continue(step.title, step.prompt)
            return True, ""

        if step.kind == CHECK:
            passed = self.console.ask_check(step.title, step.prompt, step.expect)
            return passed, "operator answered " + ("PASS" if passed else "FAIL")

        if step.kind == AUTO:
            outcome = step.action(self.ctx)
            if outcome is None:
                return True, ""
            if isinstance(outcome, tuple):
                return bool(outcome[0]), str(outcome[1])
            return bool(outcome), ""

        raise ValueError(f"Unknown step kind {step.kind!r}")

    def _handle_failure(self, step, detail):
        if step.severity == UNSAFE:
            self.console.warn(
                "UNSAFE FAILURE -- TEST STOPPED",
                f"{step.title} failed.\n{detail}\n\n"
                "Bringing the module to a safe state: high voltage to 0 V, "
                "channels off, crate switched off.  Do not touch the module "
                "until the shutdown has finished.",
            )
            self.on_unsafe()
            self.aborted = True
            self.abort_reason = f"{step.title}: {detail}"
            raise AbortFlow(self.abort_reason, unsafe=True)

        self.console.warn(
            "STEP FAILED",
            f"{step.title} failed.\n{detail}\n\n"
            "The module is not under high voltage, so the test can carry on "
            "and finish recording -- the overall result will still be FAIL.",
        )
        if not self.console.ask_yes_no("Continue with the rest of the test?", default=True):
            self.aborted = True
            self.abort_reason = f"operator stopped after {step.title}"
            raise AbortFlow(self.abort_reason, unsafe=False)

    # ----------------------------------------------------------------
    def verdict(self):
        """PASS only when every non-informational step passed and nothing aborted."""
        graded = [r for r in self.results if r.passed is not None]
        return bool(graded) and not self.aborted and all(r.passed for r in graded)

    def summary_lines(self):
        lines = []
        for step_result in self.results:
            lines.append(
                f"  {step_result.verdict():<4}  [{step_result.kind}] {step_result.title}"
                + (f" -- {step_result.detail}" if step_result.detail else "")
            )
        return lines
