"""Standalone smoke test for opening Edge and enabling Browsec.

Usage example:
    python browsec_test.py --edge-user-data-dir "/path/to/Edge/User Data" --edge-profile-directory Default

All Browsec/Edge options are parsed by avito_ad_check.py. This wrapper only
forces the dedicated --test-browsec mode so the Avito flow is not opened.
"""

from __future__ import annotations

import sys

from avito_ad_check import main


if __name__ == "__main__":
    if "--test-browsec" not in sys.argv:
        sys.argv.append("--test-browsec")
    raise SystemExit(main())
