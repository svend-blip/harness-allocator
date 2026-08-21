"""``python3 -m harness_allocator`` — run the persistent Harness Terminal."""

from __future__ import annotations

import sys

from .terminal import main

if __name__ == "__main__":
    sys.exit(main())
