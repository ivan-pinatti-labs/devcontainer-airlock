"""Shared helpers for the tests of the Python this repository writes.

Most of the Python here is a script without an extension (found by its
shebang, installed into an image), so it cannot be imported by name. `load`
reads one from its path in the tree, the way the image would run it, and
returns it as a module the tests can call into and patch. Nothing here, or
in any test, reaches the network or runs a real external command: each test
stubs those.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def load(relpath: str, name: str, **preset):
    """Execute the script at `relpath` (relative to the repository root) as
    a fresh module called `name`. `preset` names go into the module's
    namespace before its code runs, ahead of the builtins, for a script that
    reads a fixed path while it loads."""
    path = REPO_ROOT / relpath
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    module.__dict__.update(preset)
    loader.exec_module(module)
    return module


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT
