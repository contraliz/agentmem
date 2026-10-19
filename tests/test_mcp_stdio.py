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
            instructions=client.instructions or ""
            handshake_guidance=instructions[:512]
            assert "For a new KB" in handshake_guidance
            assert "confirm its ID and source root" in handshake_guidance
            assert "projects(action=register)" in handshake_guidance
            assert "index safe files" in handshake_guidance
            assert "cited evidence" in handshake_guidance and "report verified facts and gaps" in handshake_guidance
            assert "Registration creates empty scaffolding, not verified knowledge" in handshake_guidance
            tools=await client.list_tools()
            names={tool.name for tool in tools.tools}
            assert {"projects","attach_project","knowledge","index_project","check_project_changes","retrieve_evidence","tasks","claim_task","task_context","record_work","lessons","requirements","decisions","proposals","export_project","import_project"}.issubset(names)
            knowledge_schema=next(tool.input_schema for tool in tools.tools if tool.name=="knowledge")
            task_schema=next(tool.input_schema for tool in tools.tools if tool.name=="tasks")
            assert "content_offset" in knowledge_schema["properties"]
            assert {"owner","lease_token","idempotency_key","change_note","source_type","source_ref","source_date"}.issubset(task_schema["properties"])
            assert all(not re.search(r"\b(delegate|delegation|subagent|spawn|worker)\b",tool.description or "",re.I) for tool in tools.tools)
            projects_description=next(tool.description for tool in tools.tools if tool.name=="projects")
            assert "For a new KB" in projects_description and "scaffolding only" in projects_description
            result=await client.call_tool("projects",{"action":"register","project_id":"pilot","root":str(project),"description":"stdio test"})
            assert result.structured_content["id"]=="pilot"
            assert result.structured_content["root"]==str(project.resolve())
            assert result.structured_content["memory_root"]==str(tmp_path/"project-kbs"/"pilot")
            assert result.structured_content["onboarding"]["state"]=="scaffold_only"
            assert result.structured_content["onboarding"]["newly_registered"] is True
            assert result.structured_content["onboarding"]["curation_status"]=="not_started"
            rejected=await client.call_tool("projects",{"action":"unknown"})
            assert rejected.structured_content["ok"] is False
            assert rejected.structured_content["error"]["code"]=="invalid_request"
            assert rejected.structured_content["error"]["reason"] and rejected.structured_content["error"]["recovery"]
            page_list=await client.call_tool("knowledge",{"action":"list","project_id":"pilot"})
            assert page_list.structured_content["count"]==5
            page=await client.call_tool("knowledge",{"action":"read","project_id":"pilot","page_id":"purpose"})
            assert page.structured_content["content"].startswith("# Purpose and scope")
            created_page=await client.call_tool("knowledge",{"action":"create","project_id":"pilot","page_id":"mcp-notes","title":"MCP notes","content":"A unique evidence token confirms immediate indexing."})
            assert created_page.structured_content["fresh"] is True
            long_tail="Continuation reaches the final paragraph."
            long_content="# Long page\n\n"+"Policy pagination fixture. "*600+long_tail
            await client.call_tool("knowledge",{"action":"create","project_id":"pilot","page_id":"long-page","title":"Long page","content":long_content})
            first_page=await client.call_tool("knowledge",{"action":"read","project_id":"pilot","page_id":"long-page"})
            assert first_page.structured_content["content_truncated"] is True
            continuation=await client.call_tool("knowledge",{"action":"read","project_id":"pilot","page_id":"long-page","content_offset":first_page.structured_content["next_content_offset"]})
            assert continuation.structured_content["content_complete"] is True and long_tail in continuation.structured_content["content"]
            reused=await client.call_tool("projects",{"action":"register","project_id":"pilot","root":str(project),"description":"stdio test"})
            assert reused.structured_content["onboarding"]["state"]=="existing_registration"
            assert reused.structured_content["onboarding"]["newly_registered"] is False
            preserved=await client.call_tool("knowledge",{"action":"read","project_id":"pilot","page_id":"mcp-notes"})
            assert "unique evidence token" in preserved.structured_content["content"]
            page_hit=await client.call_tool("retrieve_evidence",{"project_id":"pilot","query":"unique evidence token"})
            assert page_hit.structured_content["results"][0]["citation"].startswith("@memory/knowledge/mcp-notes.md#L1-")
            page_content=await client.call_tool("knowledge",{"action":"read","project_id":"pilot","page_id":"mcp-notes"})
            updated_page=await client.call_tool("knowledge",{"action":"update","project_id":"pilot","page_id":"mcp-notes","expected_sha256":page_content.structured_content["sha256"],"content":"# MCP notes\n\nAn updated unique evidence token with a verified revision.\n"})
            assert updated_page.structured_content["updated"] is True and updated_page.structured_content["fresh"] is True
            history=await client.call_tool("knowledge",{"action":"history","project_id":"pilot","page_id":"mcp-notes"})
            assert len(history.structured_content["history"])==1
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
            assert attached.structured_content["indexed_source_count"]==12
            assert attached.structured_content["knowledge_pages"]["count"]==7
            assert "No source files have been indexed yet." not in attached.structured_content["unknowns"]
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
            assert {item["path"] for item in manifest.structured_content["indexed_sources"]}=={
                "README.md","@memory/PROJECT.md","@memory/requirements.md","@memory/decisions.md","@memory/approvals.md",
                *{f"@memory/knowledge/{page_id}.md" for page_id in ("purpose","architecture","risks","history","materials","mcp-notes","long-page")},
            }
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
            lesson=await client.call_tool("lessons",{"action":"propose","project_id":"pilot","title":"Check retry budget","trigger_text":"When verifying retry limit behavior","procedure":"Compare the current retry count to indexed documentation.","evidence":["README.md#L1-L1"],"environment":{"os":"test"},"confirmed_cause":"Retry setting was changed.","hypotheses":["The caller may retry upstream."]})
            assert lesson.structured_content["environment"]=={"os":"test"}
            assert lesson.structured_content["hypotheses"]==["The caller may retry upstream."]
            reviewed=await client.call_tool("lessons",{"action":"review","project_id":"pilot","lesson_id":lesson.structured_content["id"],"status":"accepted"})
            assert reviewed.structured_content["status"]=="accepted"
            claim=await client.call_tool("claim_task",{"project_id":"pilot","task_id":first_id,"owner":"test-codex","lease_seconds":120})
            token=claim.structured_content["lease_token"]
            blank_key=await client.call_tool("tasks",{"action":"revise-acceptance","project_id":"pilot","task_id":first_id,"owner":"test-codex","lease_token":token,"acceptance":["evidence saved"],"idempotency_key":" "})
            assert blank_key.structured_content["ok"] is False
            assert blank_key.structured_content["error"]["code"]=="idempotency_error"
            assert blank_key.structured_content["error"]["reason"] and blank_key.structured_content["error"]["recovery"]
            acceptance=await client.call_tool("tasks",{"action":"revise-acceptance","project_id":"pilot","task_id":first_id,"owner":"test-codex","lease_token":token,"acceptance":["evidence saved","source checked"],"idempotency_key":"mcp-acceptance-revision","change_note":"Add source verification to the caller-provided acceptance.","source_type":"user_instruction","source_ref":"MCP integration fixture","source_date":"2026-10-19"})
            assert acceptance.structured_content["acceptance_revision"]==2
            acceptance_history=await client.call_tool("tasks",{"action":"history","project_id":"pilot","task_id":first_id})
            assert acceptance_history.structured_content["history"][-1]["criteria"]==["evidence saved","source checked"]
            task_packet=await client.call_tool("task_context",{"project_id":"pilot","task_id":first_id})
            assert task_packet.structured_content["task"]["id"]==first_id
            assert task_packet.structured_content["applicable_accepted_lessons"]
            assert not re.search(r"\b(delegate|delegation|subagent|spawn|worker)\b",str(task_packet.structured_content),re.I)
            cp=await client.call_tool("record_work",{"action":"checkpoint","project_id":"pilot","task_id":first_id,"owner":"test-codex","lease_token":token,"checkpoint":{"last_verified":"retry limit four","next_action":"document it"}})
            assert cp.structured_content["checkpoint"]["last_verified"]=="retry limit four"
            receipt=await client.call_tool("record_work",{"action":"receipt","project_id":"pilot","task_id":first_id,"owner":"test-codex","lease_token":token,"idempotency_key":"test-1","receipt":{"status":"done","acceptance_revision":2,"lesson_ids":[lesson.structured_content["id"]],"lesson_feedback":"kept work aligned with project evidence","acceptance_checks":[{"criterion":"evidence saved","met":True,"evidence":"README.md#L1-L1"},{"criterion":"source checked","met":True,"evidence":"MCP source-history assertion"}]}})
            assert receipt.structured_content["status"]=="done"
            assert (await client.call_tool("tasks",{"action":"show","project_id":"pilot","task_id":second.structured_content["id"]})).structured_content["ready"] is True
            exported=await client.call_tool("export_project",{"project_id":"pilot"})
            assert exported.structured_content["lesson_uses"]
            requirement=await client.call_tool("requirements",{"action":"create","project_id":"pilot","title":"Keep sources cited","statement":"Factual project answers must cite source lines.","lifecycle":"current","fulfillment":"unknown","scope":{"project_wide":True},"original_quote":"Cite the sources.","source_type":"user_conversation","source_ref":"stdio test","idempotency_key":"stdio-req"})
            rid=requirement.structured_content["id"]
            assert rid.startswith("REQ-")
            listed=await client.call_tool("requirements",{"action":"list","project_id":"pilot","lifecycle":"current"})
            assert any(item["id"]==rid for item in listed.structured_content["requirements"])
            fulfilled=await client.call_tool("requirements",{"action":"fulfill","project_id":"pilot","requirement_id":rid,"expected_revision":1,"idempotency_key":"stdio-req-fulfill","fulfillment":"fulfilled","fulfillment_evidence":["stdio integration assertion"]})
            assert fulfilled.structured_content["fulfillment"]=="fulfilled" and fulfilled.structured_content["lifecycle"]=="current"
            decision=await client.call_tool("decisions",{"action":"create","project_id":"pilot","title":"Use evidence first","choice":"Return excerpts and let Codex answer.","requirement_ids":[rid],"source_type":"test","source_ref":"stdio","idempotency_key":"stdio-decision"})
            assert decision.structured_content["requirement_ids"]==[rid]
            req_task=await client.call_tool("tasks",{"action":"create","project_id":"pilot","title":"Test requirement checks","goal":"Verify source citations","acceptance":["test passes"],"requirement_ids":[rid]})
            req_context=await client.call_tool("task_context",{"project_id":"pilot","task_id":req_task.structured_content["id"]})
            req_packet=req_context.structured_content["requirements"]
            assert any(item["id"]==rid for item in req_packet["applicable_requirements"])
            assert req_packet["requirements_revision"]>=1
            req_claim=await client.call_tool("claim_task",{"project_id":"pilot","task_id":req_task.structured_content["id"],"owner":"mcp-test"})
            current_req=next(item for item in req_packet["applicable_requirements"] if item["id"]==rid)
            req_receipt=await client.call_tool("record_work",{"action":"receipt","project_id":"pilot","task_id":req_task.structured_content["id"],"owner":"mcp-test","lease_token":req_claim.structured_content["lease_token"],"idempotency_key":"mcp-requirement-receipt","receipt":{"status":"done","requirements_revision":req_packet["requirements_revision"],"requirement_checks":[{"requirement_id":rid,"revision":current_req["revision"],"met":True,"evidence":"asserted cited output"}],"acceptance_checks":[{"criterion":"test passes","met":True,"evidence":"MCP end-to-end test passed"}]}})
            assert req_receipt.structured_content["status"]=="done"
            proposal=await client.call_tool("proposals",{"action":"create","project_id":"pilot","title":"Test proposal","content":"Keep the API stable while changing internals.","targets":[{"kind":"requirement","id":rid,"revision":2}],"approval_required":True,"created_by":"test harness","idempotency_key":"stdio-proposal"})
            proposal_id=proposal.structured_content["id"]
            assert proposal.structured_content["approval"]["status"]=="not_recorded"
            approved=await client.call_tool("proposals",{"action":"approve","project_id":"pilot","proposal_id":proposal_id,"expected_revision":1,"original_quote":"Test fixture: approve this exact snapshot.","source_type":"test_fixture","source_ref":"stdio integration test","source_date":"unknown","recorded_by":"test harness","idempotency_key":"stdio-approval"})
            assert approved.structured_content["decision"]=="approved"
            current_approval=await client.call_tool("proposals",{"action":"status","project_id":"pilot","proposal_id":proposal_id})
            assert current_approval.structured_content["status"]=="approved"
            gated_task=await client.call_tool("tasks",{"action":"create","project_id":"pilot","title":"Gated rollout","goal":"Execute approved content only","approval_proposal_id":proposal_id})
            gated_context=await client.call_tool("task_context",{"project_id":"pilot","task_id":gated_task.structured_content["id"]})
            assert gated_context.structured_content["approval"]["status"]=="approved"
            assert (await client.call_tool("claim_task",{"project_id":"pilot","task_id":gated_task.structured_content["id"],"owner":"gated-test"})).structured_content["task"]["status"]=="in_progress"
            exports=await client.call_tool("export_project",{"project_id":"pilot"})
            assert exports.structured_content["schema_version"]==6 and exports.structured_content["requirements"] and exports.structured_content["decisions"]
            assert len(exports.structured_content["proposals"])==1 and len(exports.structured_content["approval_events"])==1
            assert exports.structured_content["knowledge_manifest"]["page_count"]==7
            assert attach_ms<2000 and search_ms<2000
            print(f"stdio probe attach_ms={attach_ms:.1f} search_ms={search_ms:.1f} combined_attach_query_ms={combined_ms:.1f}")

    asyncio.run(exercise())
