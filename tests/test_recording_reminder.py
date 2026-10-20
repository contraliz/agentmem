from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agentmem.store import MemoryStore, _json


def test_attach_injects_project_scoped_recording_reminder(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    store = MemoryStore(tmp_path / "state")
    store.register_project("p", root, "test project")

    context = store.attach("p")

    assert context["project"]["id"] == "p"
    assert context["recording_reminder"] == (
        "Record any important information from this work in the current project's AgentMem knowledge base."
    )
    assert "At pass end" in context["end_of_pass_summary_reminder"]
    assert "No knowledge changes" in context["end_of_pass_summary_reminder"]
    assert "Repository indexing is code indexing" in context["end_of_pass_summary_reminder"]
    compact = store.attach("p", max_chars=1000)
    assert compact["recording_reminder"] == context["recording_reminder"]
    assert compact["end_of_pass_summary_reminder"] == context["end_of_pass_summary_reminder"]
    assert len(_json(compact)) <= 1000


def test_cli_no_longer_advertises_wrapper_prompt_commands():
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "agentmem.cli", "--help"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "prompt" not in result.stdout
