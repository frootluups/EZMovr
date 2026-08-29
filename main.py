"""Entry point for SD Card Photo Mover."""

import sys
from pathlib import Path

# Ensure the package is importable when running as a script or frozen exe
if not __package__:
    sys.path.insert(0, str(Path(__file__).parent))

from sd_mover.gui import SDMoverApp


def main():
    app = SDMoverApp()
    app.mainloop()


if __name__ == "__main__":
    main()
