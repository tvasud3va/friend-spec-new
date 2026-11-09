"""Console entry point for the installed `friend` command (pyproject: friend = "friend.cli:main")."""
import sys
from pathlib import Path


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # harness modules use flat imports
    import commands
    sys.exit(commands.main())
