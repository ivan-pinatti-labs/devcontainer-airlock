"""Tests for how images/workbench/Dockerfile installs the VS Code extensions.

images/workbench/vscode/select-extensions has its own tests for the order
it prints. These check that the Dockerfile installs in that order: the check
after the install compares sorted lists, so a sort added before the install
would go unnoticed there.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "images/workbench/Dockerfile"


def install_loop() -> str:
    text = DOCKERFILE.read_text()
    start = text.index("for agent in claude codex; do")
    end = text.index('done < "/tmp/${agent}.txt";', start)
    return text[start:end]


def test_each_agent_list_comes_from_the_selector() -> None:
    assert re.search(
        r'\S+/select-extensions "\$\{agent\}" \S+/extensions\.txt'
        r' > "(\S+)/\$\{agent\}\.txt";',
        install_loop(),
    )


def test_the_install_reads_that_list_unsorted() -> None:
    loop = install_loop()
    assert "sort" not in loop
    assert "while read -r ext; do" in loop
