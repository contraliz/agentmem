from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path

from mcp import Client, StdioServerParameters


def test_stdio_server_lists_and_calls_tools(tmp_path: Path):
    root=Path(__file__).resolve().parents[1]
    project=tmp_path/"pilot"; project.mkdir(); (project/"README.md").write_text("The maximum retry limit is four attempts.\n",encoding="utf-8")
    env=os.environ.copy(); env["AGENTMEM_DATA_DIR"]=str(tmp_path/"mcp-state")
    env["AGENTMEM_MEMORY_ROOT"]=str(tmp_path/"project-kbs")
    env.pop("PYTHONPATH",None)
    params=StdioServerParameters(command=sys.executable,args=["-m","agentmem.server"],env=env,cwd=tmp_path)

    async def exercise():
        async with Client(params,read_timeout_seconds=20) as client:
            tools=await client.list_tools()
            names={tool.name for tool in tools.tools}
            assert {"projects","attach_project","index_project","check_project_changes","retrieve_evidence","tasks","claim_task","task_context","record_work","lessons","export_project"}.issubset(names)
            assert all(not re.search(r"\b(delegate|delegation|subagent|spawn|worker)\b",tool.description or "",re.I) for tool in tools.tools)
            result=await client.call_tool("projects",{"action":"register","project_id":"pilot","root":str(project),"description":"stdio test"})
            assert result.structured_content["id"]=="pilot"
            brief=await client.call_tool("update_project_brief",{"project_id":"pilot","markdown":"# Pilot memory\nThis brief is stored outside the source repository.\n"})
            assert brief.structured_content["path"]==str(tmp_path/"project-kbs"/"pilot"/"PROJECT.md")
            assert not (project/".agentmem"/"PROJECT.md").exists()
            indexed=await client.call_tool("index_project",{"project_id":"pilot","max_files":100})
            assert indexed.structured_content["project_id"]=="pilot"
            status=await client.call_tool("check_project_changes",{"project_id":"pilot"})
            assert status.structured_content["changed_count"]==0
            started=time.perf_counter()
            attached=await client.call_tool("attach_project",{"project_id":"pilot"})
            attach_ms=(time.perf_counter()-started)*1000
            assert attached.structured_content["project"]["id"]=="pilot"
            assert len(str(attached.structured_content))<6500
            assert attached.structured_content["indexed_sources"]==[]
            assert attached.structured_content["indexed_source_count"]==2
            assert not re.search(r"\b(delegate|delegation|subagent|spawn|worker)\b",str(attached.structured_content),re.I)
            started=time.perf_counter()
            query=await client.call_tool("retrieve_evidence",{"project_id":"pilot","query":"MCP server"})
            search_ms=(time.perf_counter()-started)*1000
            assert "results" in query.structured_content
            evidence=await client.call_tool("retrieve_evidence",{"project_id":"pilot","query":"retry limit"})
            assert evidence.structured_content["results"][0]["citation"].startswith("README.md#L1-")
            started=time.perf_counter()
            combined=await client.call_tool("attach_project",{"project_id":"pilot","query":"What is the retry limit?"})
            combined_ms=(time.perf_counter()-started)*1000
            combined_evidence=combined.structured_content["question_context"]
            assert combined_evidence["results"][0]["citation"].startswith("README.md#L1-")
            assert combined_evidence["results"][0]["fresh"] is True
            manifest=await client.call_tool("attach_project",{"project_id":"pilot","include_sources":True})
            assert {item["path"] for item in manifest.structured_content["indexed_sources"]}=={"README.md","@memory/PROJECT.md"}
            other=tmp_path/"other"; other.mkdir(); (other/"README.md").write_text("completely unrelated notes",encoding="utf-8")
            await client.call_tool("projects",{"action":"register","project_id":"other","root":str(other)})
            await client.call_tool("index_project",{"project_id":"other"})
            isolated=await client.call_tool("retrieve_evidence",{"project_id":"other","query":"retry limit"})
            assert not isolated.structured_content["results"]

            first=await client.call_tool("tasks",{"action":"create","project_id":"pilot","title":"Verify retries","goal":"Confirm current retry behavior","acceptance":["evidence saved"]})
            first_id=first.structured_content["id"]
            edited=await client.call_tool("tasks",{"action":"update","project_id":"pilot","task_id":first_id,"priority":2})
            assert edited.structured_content["priority"]==2
            second=await client.call_tool("tasks",{"action":"create","project_id":"pilot","title":"Update docs","goal":"Document verified retry behavior","acceptance":["doc checked"],"depends_on":[first_id]})
            assert second.structured_content["ready"] is False
            lesson=await client.call_tool("lessons",{"action":"propose","project_id":"pilot","title":"Check retry budget","trigger_text":"When verifying retry limit behavior","procedure":"Compare the current retry count to indexed documentation.","evidence":["README.md#L1-L1"]})
            reviewed=await client.call_tool("lessons",{"action":"review","project_id":"pilot","lesson_id":lesson.structured_content["id"],"status":"accepted"})
            assert reviewed.structured_content["status"]=="accepted"
            claim=await client.call_tool("claim_task",{"project_id":"pilot","task_id":first_id,"owner":"test-codex","lease_seconds":120})
            token=claim.structured_content["lease_token"]
            task_packet=await client.call_tool("task_context",{"project_id":"pilot","task_id":first_id})
            assert task_packet.structured_content["task"]["id"]==first_id
            assert task_packet.structured_content["applicable_accepted_lessons"]
            assert not re.search(r"\b(delegate|delegation|subagent|spawn|worker)\b",str(task_packet.structured_content),re.I)
            cp=await client.call_tool("record_work",{"action":"checkpoint","project_id":"pilot","task_id":first_id,"owner":"test-codex","lease_token":token,"checkpoint":{"last_verified":"retry limit four","next_action":"document it"}})
            assert cp.structured_content["checkpoint"]["last_verified"]=="retry limit four"
            receipt=await client.call_tool("record_work",{"action":"receipt","project_id":"pilot","task_id":first_id,"owner":"test-codex","lease_token":token,"idempotency_key":"test-1","receipt":{"status":"done","lesson_ids":[lesson.structured_content["id"]],"lesson_feedback":"kept work aligned with project evidence","acceptance_checks":[{"criterion":"evidence saved","met":True,"evidence":"README.md#L1-L1"}]}})
            assert receipt.structured_content["status"]=="done"
            assert (await client.call_tool("tasks",{"action":"show","project_id":"pilot","task_id":second.structured_content["id"]})).structured_content["ready"] is True
            exported=await client.call_tool("export_project",{"project_id":"pilot"})
            assert exported.structured_content["lesson_uses"]
            assert attach_ms<2000 and search_ms<2000
            print(f"stdio probe attach_ms={attach_ms:.1f} search_ms={search_ms:.1f} combined_attach_query_ms={combined_ms:.1f}")

    asyncio.run(exercise())
