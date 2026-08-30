"""PyInstaller entry point for the frozen WheelHat build.

Order matters here:

1. Run the bootstrap. It picks the data folder, downloads or repairs PySide6,
   and puts it on sys.path. Nothing before this line may import Qt, and nothing
   may import wheelhat.config either - config resolves its data folder at import
   time from WHEELHAT_DATA_DIR, which the bootstrap is what sets.
2. Only then hand over to the normal entry point.

Running from source this file is unused: `python -m wheelhat` goes straight to
__main__, where PySide6 comes from the virtualenv.
"""

import multiprocessing
import sys

from wheelhat.bootstrap import run as bootstrap

if __name__ == "__main__":
    # A frozen child process would otherwise re-run the whole application.
    multiprocessing.freeze_support()
    bootstrap()

    from wheelhat.__main__ import main

    sys.exit(main())
