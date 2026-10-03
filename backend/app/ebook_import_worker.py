"""Apply a kernel file-size bound before launching an optional converter."""
from __future__ import annotations

import os
import sys


def main() -> None:
    limit = int(sys.argv[1])
    if sys.platform != "win32":
        import resource
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    os.execv(sys.argv[2], sys.argv[2:])


if __name__ == "__main__":
    main()
