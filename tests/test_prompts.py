from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentmem.prompts import build_prompt
from agentmem.store import MemoryStore


def test_prompt_payload_is_scoped_and_non_recursive():
    startup = build_prompt("start", "pilot")
    end_pass = build_prompt("end-pass", "pilot", "wrapper-turn-17")

    assert startup["project_id"] == "pilot"
    assert "exact project_id" in startup["prompt"]
    assert "page inventory" in startup["prompt"]
    assert "pagination and completeness markers" in startup["prompt"]
    assert end_pass["pass_id"] == "wrapper-turn-17"
    assert "only durable information from that caller work pass" in end_pass["prompt"]
    assert "same agent that handled the pass" in end_pass["prompt"]
    assert "do not request or trigger another end-pass prompt" in end_pass["prompt"]
    assert "caller controls pass boundaries and delivery" in end_pass["prompt"]
    assert "`no_changes`" in end_pass["prompt"] and "`write_failure`" in end_pass["prompt"]


@pytest.mark.parametrize(("phase", "project_id", "pass_id"), [
    ("start", "  ", ""),
    ("end-pass", "pilot", " "),
    ("unknown", "pilot", "turn"),
])
def test_prompt_payload_rejects_missing_scope_or_boundary(phase, project_id, pass_id):
    with pytest.raises(ValueError):
        build_prompt(phase, project_id, pass_id)


def test_cli_prints_prompt_json_only_for_registered_projects(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    data_dir = tmp_path / "agentmem-data"
    MemoryStore(data_dir).register_project("pilot", root, "prompt test")
    repo = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [sys.executable, "-m", "agentmem.cli", "--data-dir", str(data_dir), "prompt",
         "end-pass", "--project-id", "pilot", "--pass-id", "turn-3"],
        cwd=repo, text=True, capture_output=True, check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["phase"] == "end-pass"
    assert payload["project_id"] == "pilot"
    assert payload["pass_id"] == "turn-3"
    assert "no durable delta" in payload["prompt"]

    help_result = subprocess.run(
        [sys.executable, "-m", "agentmem.cli", "--help"],
        cwd=repo, text=True, capture_output=True, check=True,
    )
    assert "prompt" in help_result.stdout
    assert "hooks" not in help_result.stdout
