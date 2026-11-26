import sys
from pathlib import Path

# Ensure worktree's huntbot2 package is resolved before the editable-install copy
# from the main repo. This is needed because pyproject.toml's pythonpath=["."]
# resolves relative to the project root rather than the worktree rootdir.
_worktree = Path(__file__).resolve().parent
if str(_worktree) not in sys.path:
    sys.path.insert(0, str(_worktree))
