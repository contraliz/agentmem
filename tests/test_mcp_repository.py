from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters


def test_stdio_exposes_repository_service_and_agent_context(tmp_path: Path):
    checkout = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("The service currently retries three times.\n", encoding="utf-8")
    env = os.environ.copy()
    env["AGENTMEM_DATA_DIR"] = str(tmp_path / "state")
    env["AGENTMEM_MEMORY_ROOT"] = str(tmp_path / "knowledge")
    env.pop("PYTHONPATH", None)
    params = StdioServerParameters(command=sys.executable, args=["-m", "agentmem.server"], env=env, cwd=checkout)

    async def exercise():
        async with Client(params, read_timeout_seconds=20) as client:
            instructions = client.instructions or ""
            assert "Repository search returns source citations and freshness" in instructions
            assert "At the end of each work pass" in instructions
            assert "does not detect every pass" in instructions
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert {"attach_project", "knowledge", "retrieve_evidence", "repository_register",
                    "repository_list", "repository_status", "repository_search", "repository_resync"}.issubset(names)

            registered_project = await client.call_tool("projects", {
                "action": "register", "project_id": "pilot", "root": str(source), "description": "repository MCP test"
            })
            assert registered_project.structured_content["id"] == "pilot"
            attached = await client.call_tool("attach_project", {"project_id": "pilot"})
            catalog = attached.structured_content["service_catalog"]
            assert catalog["knowledge_base"]["available"] is True
            assert catalog["repository_indexing"]["available"] is True
            assert attached.structured_content["end_of_pass_summary_reminder"]
            compact = await client.call_tool("attach_project", {"project_id": "pilot", "max_chars": 1000})
            assert len(json.dumps(compact.structured_content, ensure_ascii=False)) <= 1000
            assert "service_catalog" in compact.structured_content

            repo = await client.call_tool("repository_register", {
                "project_id": "pilot", "repository_root": str(source)
            })
            repo_id = repo.structured_content["repository_id"]
            assert repo.structured_content["initial_sync"]["files_changed"] >= 1
            refreshed = await client.call_tool("attach_project", {"project_id": "pilot"})
            configured = refreshed.structured_content["service_catalog"]["repository_indexing"]["repositories"]
            assert configured[0]["repository_id"] == repo_id
            assert configured[0]["index_revision"] >= 1
            evidence = await client.call_tool("repository_search", {
                "project_id": "pilot", "repository_id": repo_id, "query": "three times"
            })
            assert evidence.structured_content["results"][0]["citation"].startswith("README.md#L1")
            assert evidence.structured_content["results"][0]["fresh"] is True

            (source / "README.md").write_text("The service now retries five times.\n", encoding="utf-8")
            resync = await client.call_tool("repository_resync", {"project_id": "pilot", "repository_id": repo_id})
            assert resync.structured_content["files_changed"] == 1
            status = await client.call_tool("repository_status", {"project_id": "pilot", "repository_id": repo_id})
            assert status.structured_content["index_revision"] >= 2
            listed = await client.call_tool("repository_list", {"project_id": "pilot"})
            assert listed.structured_content["repositories"][0]["repository_id"] == repo_id

    asyncio.run(exercise())
