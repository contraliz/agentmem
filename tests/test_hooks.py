import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agentmem.hooks import HOOK_MARKER, REVIEW_PROMPT, decide, install, status, uninstall
from agentmem.store import MemoryStore


def _registered(tmp_path: Path, *, nested: bool = False):
    root = tmp_path / "project with spaces"
    root.mkdir()
    data = tmp_path / "data"
    memory = tmp_path / "memory"
    store = MemoryStore(data, memory)
    project = store.register_project("pilot", root, "test project")
    if nested:
        child = root / "child"
        child.mkdir()
        store.register_project("child", child, "nested project")
    return root, data, store, project


def _payload(root: Path, session="session-1", turn="turn-1", active=False):
    return {"hook_event_name": "Stop", "cwd": str(root), "session_id": session,
            "turn_id": turn, "stop_hook_active": active}


def test_stop_contract_requests_one_same_instance_review_per_turn(tmp_path):
    root, data, _, _ = _registered(tmp_path)

    first = decide(_payload(root), "pilot", str(root), str(data))
    assert first == {"decision": "block", "reason": REVIEW_PROMPT}
    assert "same-instance" in first["reason"] or "do not request another continuation" in first["reason"]

    # Codex identifies the requested continuation via stop_hook_active.
    assert decide(_payload(root, active=True), "pilot", str(root), str(data)) == {}
    # Duplicate callbacks for the same session/turn are idempotent.
    assert decide(_payload(root), "pilot", str(root), str(data)) == {}
    # A later normal caller turn is still eligible.
    assert decide(_payload(root, turn="turn-2"), "pilot", str(root), str(data))["decision"] == "block"
    # Session IDs scope the idempotency key too.
    assert decide(_payload(root, session="session-2"), "pilot", str(root), str(data))["decision"] == "block"

    state = data / "hook-state"
    db_path = next(state.glob("*.sqlite3"))
    with sqlite3.connect(db_path) as db:
        columns = [row[1] for row in db.execute("PRAGMA table_info(reviewed_passes)")]
        keys = [row[0] for row in db.execute("SELECT pass_key FROM reviewed_passes")]
    assert columns == ["pass_key", "created_at"]
    assert all(len(key) == 64 for key in keys)


@pytest.mark.parametrize("payload", [None, [], "bad", {"cwd": ""},
                                     {"cwd": ".", "session_id": "s", "turn_id": "t", "stop_hook_active": "false"},
                                     {"hook_event_name": "PreToolUse", "cwd": ".", "session_id": "s", "turn_id": "t", "stop_hook_active": False}])
def test_malformed_hook_inputs_never_request_continuation(tmp_path, payload):
    root, data, _, _ = _registered(tmp_path)
    result = decide(payload, "pilot", str(root), str(data))
    assert result.get("decision") != "block"
    assert isinstance(result, dict)


def test_wrong_or_more_specific_project_scope_is_skipped(tmp_path):
    root, data, _, _ = _registered(tmp_path, nested=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    assert "decision" not in decide(_payload(outside), "pilot", str(root), str(data))
    nested = root / "child"
    assert "decision" not in decide(_payload(nested), "pilot", str(root), str(data))
    with pytest.raises(ValueError, match="registered project root"):
        install("pilot", str(outside), str(data))


def test_project_local_install_is_idempotent_and_uninstall_preserves_other_hooks(tmp_path):
    root, data, _, _ = _registered(tmp_path)
    config_path = root / ".codex" / "hooks.json"
    original = {
        "description": "user project hooks",
        "otherMetadata": {"keep": True},
        "hooks": {
            "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "echo user"}]}],
            "SessionStart": [{"hooks": [{"type": "command", "command": "echo start"}]}],
        },
    }
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(original), encoding="utf-8")

    installed = install("pilot", str(root), str(data), python_exe=r"C:\Python 3.13\python.exe")
    assert installed["handlers"] == 1
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["description"] == original["description"]
    assert config["otherMetadata"] == original["otherMetadata"]
    assert config["hooks"]["SessionStart"] == original["hooks"]["SessionStart"]
    group = config["hooks"]["Stop"][0]
    assert group["matcher"] == ""
    managed = next(h for g in config["hooks"]["Stop"] for h in g["hooks"] if HOOK_MARKER in h.get("commandWindows", ""))
    assert "python3" in managed["command"]
    assert r"C:\Python 3.13\python.exe" in managed["commandWindows"]
    assert status("pilot", str(root), str(data))["handlers"] == 1

    install("pilot", str(root), str(data))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert sum(HOOK_MARKER in h.get("commandWindows", "") for g in config["hooks"]["Stop"] for h in g["hooks"]) == 1

    removed = uninstall("pilot", str(root), str(data))
    assert removed["removed"] == 1
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["description"] == original["description"]
    assert config["otherMetadata"] == original["otherMetadata"]
    assert config["hooks"]["Stop"] == original["hooks"]["Stop"]
    assert config["hooks"]["SessionStart"] == original["hooks"]["SessionStart"]
    assert status("pilot", str(root), str(data))["status"] == "not_installed"


def test_installer_refuses_malformed_config_without_overwrite(tmp_path):
    root, data, _, _ = _registered(tmp_path)
    config_path = root / ".codex" / "hooks.json"
    config_path.parent.mkdir()
    contents = "{not json"
    config_path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError, match="Cannot safely read"):
        install("pilot", str(root), str(data))
    assert config_path.read_text(encoding="utf-8") == contents


def test_installer_collapses_duplicate_managed_entries_without_touching_others(tmp_path):
    root, data, _, _ = _registered(tmp_path)
    config_path = root / ".codex" / "hooks.json"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps({"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "old " + HOOK_MARKER}]},
        {"matcher": "x", "hooks": [{"type": "command", "command": "echo keep"},
                                      {"type": "command", "command": "duplicate " + HOOK_MARKER}]},
    ]}}), encoding="utf-8")
    result = install("pilot", str(root), str(data))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    managed = [h for group in config["hooks"]["Stop"] for h in group["hooks"] if _has_marker(h)]
    assert result["handlers"] == 1
    assert len(managed) == 1
    assert any(h.get("command") == "echo keep" for group in config["hooks"]["Stop"] for h in group["hooks"])


def test_actual_module_callback_emits_stop_protocol_json(tmp_path):
    root, data, _, _ = _registered(tmp_path)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    env["AGENTMEM_DATA_DIR"] = str(data)
    payload = json.dumps(_payload(root))
    result = subprocess.run(
        [sys.executable, "-m", "agentmem.hooks", "callback", "--project-id", "pilot",
         "--root", str(root), "--data-dir", str(data), HOOK_MARKER],
        input=payload, text=True, capture_output=True, env=env, check=True,
    )
    wire = json.loads(result.stdout)
    assert wire["decision"] == "block"
    assert wire["reason"] == REVIEW_PROMPT
    assert result.stderr == ""


def _has_marker(handler):
    return HOOK_MARKER in handler.get("command", "") + " " + handler.get("commandWindows", "")


def test_cli_installer_actions_use_registered_project_scope(tmp_path):
    root, data, _, _ = _registered(tmp_path)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    cli = [sys.executable, "-m", "agentmem.cli", "--data-dir", str(data), "hooks"]

    installed = subprocess.run(cli + ["install", "--project-id", "pilot", "--root", str(root)],
                               text=True, capture_output=True, env=env, check=True)
    assert json.loads(installed.stdout)["status"] == "installed"
    current = subprocess.run(cli + ["status", "--project-id", "pilot", "--root", str(root)],
                             text=True, capture_output=True, env=env, check=True)
    assert json.loads(current.stdout)["handlers"] == 1
    removed = subprocess.run(cli + ["uninstall", "--project-id", "pilot", "--root", str(root)],
                             text=True, capture_output=True, env=env, check=True)
    assert json.loads(removed.stdout)["removed"] == 1
