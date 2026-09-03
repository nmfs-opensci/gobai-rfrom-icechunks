#!/usr/bin/env python3
"""Back-compat shim: this script now lives at the repository root as ``nodd.py``.

GOBAI HR (GitHub issue #13) shares RFROM's grid and therefore its pipeline, so
the batch script was promoted to a product-parameterized ``nodd.py`` covering
both. Every flag is unchanged, so existing commands keep working:

    python RFROMV/rfrom_nodd.py --stream temp_stable --all     # via this shim
    python nodd.py             --stream temp_stable --all      # equivalent

Prefer ``nodd.py`` in new work. See RFROMV/README.md and GOBAI-O2/README.md.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nodd import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
