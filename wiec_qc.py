"""Backward-compatible shim for the legacy WIEC QC entry point.

The real implementation lives in WIEC_Component/wiec_wib_qc.py and follows the
package-item pattern: NAME, KEY, DESCRIPTION, EST_MINUTES, run(session).
"""

from WIEC_Component.wiec_wib_qc import (
    NAME,
    KEY,
    DESCRIPTION,
    EST_MINUTES,
    run,
    main,
)

__all__ = ["NAME", "KEY", "DESCRIPTION", "EST_MINUTES", "run", "main"]


if __name__ == "__main__":
    raise SystemExit(main())