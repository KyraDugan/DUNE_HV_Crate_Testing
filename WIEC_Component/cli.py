"""Standalone entry point shared by the individual item modules.

Lets any item run on its own, e.g.

    python3 -m WIEC_Component.heater_test config.json my_test_name

which builds a session of its own (its own results directory and its own
spreadsheet row) and powers everything down again on the way out.
"""

import sys
import traceback


def run_single_item(module):
    from .session import TestSession

    if len(sys.argv) < 2:
        sys.exit(
            f"Usage: python3 -m WIEC_Component.{getattr(module, 'KEY', 'item')} "
            "<config file> [test name]"
        )

    config_file = sys.argv[1]
    test_name = sys.argv[2] if len(sys.argv) > 2 else input("Input the test name:\n")

    session = TestSession(config_file, test_name)
    try:
        passed = module.run(session)
        session.record(module.KEY, passed)
    except BaseException:
        print(traceback.format_exc())
        session.record(module.KEY, False)
        raise
    finally:
        try:
            session.emergency_shutoff()
            session.finalize()
        finally:
            session.close_instruments()

    return 0 if passed else 1
