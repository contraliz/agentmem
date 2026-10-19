from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from agentmem.store import MemoryStore, _json


@pytest.fixture
def store(tmp_path):
    source=tmp_path/"source"; source.mkdir()
    result=MemoryStore(tmp_path/"state",tmp_path/"kbs")
    result.register_project("p",source,"policy fixture")
    other=tmp_path/"other"; other.mkdir()
    result.register_project("q",other,"other policy fixture")
    return result,source


def test_receipt_requires_exact_saved_criteria_and_idempotent_payload(store):
    memory,_=store
    task=memory.create_task("p","two criteria","verify both",["first","second"])
    lease=memory.claim_task("p",task["id"],"worker")
    cases=[
        ([{"criterion":"first","met":True,"evidence":"ok"}],"missing"),
        ([{"criterion":"first","met":True,"evidence":"ok"},{"criterion":"first","met":True,"evidence":"ok"}],"duplicate_or_excess"),
        ([{"criterion":"first","met":True,"evidence":"ok"},{"criterion":"other","met":True,"evidence":"ok"}],"unknown"),
    ]
    for checks,field in cases:
        result=memory.record_receipt("p",task["id"],"worker",lease["lease_token"],{"status":"done","acceptance_checks":checks},"key-"+field)
        assert result["status"]=="in_progress" and result["acceptance_coverage"][field]
    exact={"status":"done","acceptance_checks":[{"criterion":"first","met":True,"evidence":"ok"},{"criterion":"second","met":True,"evidence":"ok"}]}
    result=memory.record_receipt("p",task["id"],"worker",lease["lease_token"],exact,"exact")
    assert result["completion_accepted"]
    assert memory.record_receipt("p",task["id"],"worker","stale-token",exact,"exact")["duplicate"]
    reordered={"acceptance_checks":exact["acceptance_checks"],"status":"done"}
    assert memory.record_receipt("p",task["id"],"worker","stale-token",reordered,"exact")["duplicate"]
    with pytest.raises(ValueError,match="different receipt content"):
        memory.record_receipt("p",task["id"],"worker","stale-token",{"status":"done"},"exact")
    for bad in ("", " \t ", None, 3):
        with pytest.raises(ValueError,match="idempotency_key"):
            memory.create_requirement("p","Bad key","Statement",idempotency_key=bad)


def test_in_progress_acceptance_revision_is_source_attributed_and_required_by_receipt(store):
    memory,_=store
    task=memory.create_task("p","Authorized delivery","Commit then push",["commit exists","no push"])
    claim=memory.claim_task("p",task["id"],"worker")
    new=["commit exists","authorized push verified on remote"]
    changed=memory.revise_task_acceptance("p",task["id"],"worker",claim["lease_token"],new,"user-push-1",
        'User update: "oh my bad, do push the changes when you are done"',"user_instruction","direct user follow-up","2026-10-19")
    assert changed["acceptance_revision"]==2
    history=memory.task_acceptance_history("p",task["id"])["history"]
    assert history[-1]["criteria"]==new and history[-1]["source_type"]=="user_instruction"
    assert memory.revise_task_acceptance("p",task["id"],"worker","stale",new,"user-push-1",
        'User update: "oh my bad, do push the changes when you are done"',"user_instruction","direct user follow-up","2026-10-19")["acceptance_change_duplicate"]
    old_checks=[{"criterion":"commit exists","met":True,"evidence":"commit"},{"criterion":"no push","met":True,"evidence":"not pushed yet"}]
    stale=memory.record_receipt("p",task["id"],"worker",claim["lease_token"],{"status":"done","acceptance_checks":old_checks},"old-criteria")
    assert not stale["completion_accepted"] and stale["acceptance_coverage"]["revision_matches"] is False
    current={"status":"done","acceptance_revision":2,"acceptance_checks":[
        {"criterion":value,"met":True,"evidence":"verified by task receipt"} for value in new]}
    assert memory.record_receipt("p",task["id"],"worker",claim["lease_token"],current,"current-criteria")["completion_accepted"]


def test_requirement_supersession_rejects_self_cycle_and_missing_target(store):
    memory,_=store
    a=memory.create_requirement("p","A","Statement A",idempotency_key="a")
    b=memory.create_requirement("p","B","Statement B",supersedes_id=a["id"],idempotency_key="b")
    with pytest.raises(ValueError,match="itself"):
        memory.update_requirement("p",a["id"],expected_revision=1,idempotency_key="self",supersedes_id=a["id"])
    with pytest.raises(ValueError,match="cycle"):
        memory.update_requirement("p",a["id"],expected_revision=1,idempotency_key="cycle",supersedes_id=b["id"])
    with pytest.raises(ValueError,match="existing requirement"):
        memory.update_requirement("p",a["id"],expected_revision=1,idempotency_key="missing",supersedes_id="REQ-9999")
    a_scope=memory.update_requirement("p",a["id"],expected_revision=1,idempotency_key="scope-a",scope={"components":["policy"]})
    memory.update_requirement("p",b["id"],expected_revision=1,idempotency_key="scope-b",scope={"components":["policy"]})
    task=memory.create_task("p","scoped","verify inheritance",components=["policy"])
    linked_task=memory.create_task("p","linked scoped","verify explicit links",requirement_ids=[a["id"]])
    with memory.connect("p") as db:
        db.execute("UPDATE requirements SET supersedes_id=? WHERE id=?",(b["id"],a["id"]))
        db.execute("UPDATE requirements SET supersedes_id=? WHERE id=?",(a["id"],b["id"]))
    with pytest.raises(ValueError,match="graph contains a cycle"):
        memory.task_context("p",task["id"])
    with pytest.raises(ValueError,match="graph contains a cycle"):
        memory.task_context("p",linked_task["id"])
    with memory.connect("p") as db:
        db.execute("UPDATE requirements SET supersedes_id='REQ-9999' WHERE id=?",(b["id"],))
    with pytest.raises(ValueError,match="references missing target"):
        memory.task_context("p",linked_task["id"])


def test_empty_fulfillment_evidence_cannot_bypass_merged_state(store):
    memory,_=store
    req=memory.create_requirement("p","Evidence","Must preserve evidence",fulfillment="fulfilled",
                                  fulfillment_evidence=["initial check"],idempotency_key="req")
    with pytest.raises(ValueError,match="resulting record"):
        memory.update_requirement("p",req["id"],expected_revision=1,idempotency_key="clear",fulfillment_evidence=[])
    current=memory.get_requirement("p",req["id"])
    assert current["fulfillment_evidence"]==["initial check"]


def test_projection_failure_reports_committed_record_and_retry_preserves_manual_text(store,monkeypatch):
    memory,_=store
    original=memory.sync_records_projection
    calls=0
    def fail_once(project_id,kind,*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==1: raise OSError("private local failure path")
        return original(project_id,kind,*args,**kwargs)
    monkeypatch.setattr(memory,"sync_records_projection",fail_once)
    req=memory.create_requirement("p","Committed","Visible after filesystem projection failure",idempotency_key="committed")
    assert req["projection_status"]["status"]=="failed" and req["projection_status"]["record_committed"]
    assert req["record_committed"] and req["record_revision"]==1 and req["recovery"]
    assert memory.get_requirement("p",req["id"])["statement"]==req["statement"]
    target=Path(memory.get_project("p")["memory_root"])/"requirements.md"
    target.write_text("# Human edits\n\nKeep this note.\n",encoding="utf-8")
    with pytest.raises(ValueError,match="Manual edits"):
        memory.sync_records_projection("p","requirements")
    assert "Keep this note" in target.read_text(encoding="utf-8")
    recovered=memory.sync_records_projection("p","requirements",force=True)
    assert recovered["record_count"]==1


def test_content_freshness_detects_preserved_metadata_and_deep_hit_excerpt(store):
    memory,source=store
    deep="zqneedleofinterest"
    body="opening material that is deliberately very long "*100+deep+" concluding text"
    path=source/"deep.md"; path.write_text(body,encoding="utf-8")
    memory.index_project("p")
    old=path.stat(); result=memory.search("p",deep)["results"][0]
    assert deep in result["excerpt"] and result["freshness_level"]=="content_checked"
    attached=memory.attach("p",max_chars=1000,query=deep)
    assert len(_json(attached))<=1000
    attached_results=attached.get("question_context",{}).get("results",[])
    assert not attached_results or deep in attached_results[0]["excerpt"]
    changed=body.replace(deep,"changed-token-here")
    path.write_text(changed,encoding="utf-8")
    os.utime(path,ns=(old.st_atime_ns,old.st_mtime_ns))
    stale=memory.search("p",deep)["results"][0]
    assert stale["freshness_level"]=="content_changed" and stale["fresh"] is False
    path.unlink()
    assert memory.search("p",deep)["results"][0]["freshness_level"]=="source_unavailable"


def test_chinese_fallback_excerpt_centers_on_actual_bigram(store):
    memory,source=store
    hit="目标命中内容"
    path=source/"zh.md"; path.write_text(("开头描述 "*700)+hit,encoding="utf-8")
    memory.index_project("p")
    found=memory.search("p",hit)
    assert found["results"] and hit[-2:] in found["results"][0]["excerpt"]
    assert found["results"][0]["excerpt_char_start"]>0


def test_lexical_evaluation_exact_overlap_cross_topic_and_project_isolation(store):
    memory,source=store
    (source/"operations.md").write_text("zqalpha recovery should preserve the projection after commit.",encoding="utf-8")
    (source/"design.md").write_text("zqalpha palette colors and typography guide the visual theme.",encoding="utf-8")
    memory.index_project("p")
    assert memory.search("p","zqalpha projection commit")["results"][0]["path"]=="operations.md"
    # This paraphrase shares discriminating words but is still lexical retrieval,
    # not a semantic-search guarantee.
    assert memory.search("p","preserve the projection after a commit")["results"][0]["path"]=="operations.md"
    assert memory.search("p","zqalpha palette typography")["results"][0]["path"]=="design.md"
    assert memory.search("q","zqalpha projection commit")["results"]==[]


def test_secret_shaped_content_is_never_indexed_and_removed_on_reindex(store):
    memory,source=store
    secret="credential-like-value-built-at-runtime"
    candidate=source/"notes.md"
    candidate.write_text("Ordinary note\n",encoding="utf-8")
    memory.index_project("p")
    candidate.write_text("api_key="+secret+"\n",encoding="utf-8")
    result=memory.index_project("p")
    assert result["files_skipped"]>=1
    assert memory.search("p",secret)["results"]==[]


def test_knowledge_cas_update_and_delete_preserve_retrievable_history(store):
    memory,_=store
    created=memory.create_knowledge_page("p","retention","Retention","A specific durable note.")
    before=memory.read_knowledge_page("p","retention")
    updated=memory.update_knowledge_page("p","retention","# Retention\n\nA corrected durable note.\n",before["sha256"])
    old=memory.read_knowledge_page("p","retention",history_id=updated["history_id"])
    assert old["path"]==old["citation"].split("#")[0]
    assert ".history/retention/" in old["citation"]
    with pytest.raises(ValueError,match="changed since it was read"):
        memory.delete_knowledge_page("p","retention",before["sha256"])
    current=memory.read_knowledge_page("p","retention")
    deleted=memory.delete_knowledge_page("p","retention",current["sha256"])
    assert deleted["deleted_from_current"] and deleted["retained_in_history"] and deleted["erasure_complete"] is False
    assert memory.search("p","corrected durable note")["results"]==[]
    assert memory.read_knowledge_page("p","retention",history_id=deleted["history_id"])["content"].startswith("# Retention")


def test_lessons_return_deterministic_applicability_and_legacy_uncertainty(store):
    memory,_=store
    scoped=memory.propose_lesson("p","Windows Python setup","setup interpreter failure","Use py launcher",
        ["setup.md#L1"],environment={"os":"windows","python":"3.13"},confirmed_cause="Wrong pyproject floor",
        preconditions=["py launcher installed"],exclusions=[{"os":"linux"}],hypotheses=["PATH may also be incomplete"])
    assert scoped["confirmed_cause"]=="Wrong pyproject floor" and scoped["hypotheses"]==["PATH may also be incomplete"]
    memory.review_lesson("p",scoped["id"],"accepted")
    legacy=memory.propose_lesson("p","Generic setup","setup interpreter failure","Inspect Python path",["setup.md#L1"])
    memory.review_lesson("p",legacy["id"],"accepted")
    missing=memory.search_lessons("p","setup interpreter failure",environment={})
    assert {x["id"]:x["applicability"]["outcome"] for x in missing}=={scoped["id"]:"insufficient_information",legacy["id"]:"insufficient_information"}
    linux=memory.search_lessons("p","setup interpreter failure",environment={"os":"linux","python":"3.13"})
    assert next(x for x in linux if x["id"]==scoped["id"])["applicability"]["outcome"]=="not_applicable"
    windows=memory.search_lessons("p","setup interpreter failure",environment={"os":"windows","python":"3.13"})
    assert next(x for x in windows if x["id"]==scoped["id"])["applicability"]["outcome"]=="applicable"


def test_approval_context_is_compact_and_task_packet_stays_bounded(store):
    memory,_=store
    proposal=memory.create_proposal("p","Large quote proposal","compact proposal summary",approval_required=True,idempotency_key="proposal")
    memory.approve_proposal("p",proposal["id"],expected_revision=1,original_quote="Q"*12000,source_type="user",source_ref="chat",source_date="2026-10-19",recorded_by="user",idempotency_key="approve")
    task=memory.create_task("p","Bounded approval","Carry approval context",approval_proposal_id=proposal["id"])
    packet=memory.task_context("p",task["id"],max_chars=1500)
    assert len(_json(packet))<=1500
    quote=(packet["approval"].get("latest_event") or {}).get("original_quote_summary") or packet["approval"].get("quote_continuation")
    assert quote["characters"]==12000 and quote["retrieve"]=="proposals(action='approval-history')"


def test_export_import_roundtrip_structured_records_and_leases(tmp_path):
    source=tmp_path/"source"; source.mkdir(); data=tmp_path/"data"; memory_root=tmp_path/"kbs"
    first=MemoryStore(data,memory_root); first.register_project("p",source,"roundtrip")
    req=first.create_requirement("p","Rule","Retain sources",criteria=["retention"],idempotency_key="r")
    task=first.create_task("p","Task","Do it",["verify"],requirement_ids=[req["id"]])
    claim=first.claim_task("p",task["id"],"worker")
    lesson=first.propose_lesson("p","Lesson","before verify","check data",["notes#L1"],environment={"os":"test"})
    first.review_lesson("p",lesson["id"],"accepted")
    exported=first.export_project("p")
    second=MemoryStore(tmp_path/"second-data",tmp_path/"second-kbs")
    second.register_project("p",source,"roundtrip destination")
    result=second.import_project("p",exported)
    assert result["record_committed"] and result["derived_index_imported"] is False
    assert second.get_requirement("p",req["id"])["criteria"]==["retention"]
    assert second.get_lesson("p",lesson["id"])["environment"]=={"os":"test"}
    imported=second.get_task("p",task["id"])
    assert imported["owner"]=="" and "lease_token" not in imported
    assert second.export_project("p")["requirements"]==exported["requirements"]
    assert second.task_acceptance_history("p",task["id"])["history"]
    with pytest.raises(ValueError,match="already contains"):
        second.import_project("p",exported)
    assert claim["lease_token"]


def test_v9_schema_upgrade_backfills_acceptance_history_without_changing_criteria(tmp_path):
    source=tmp_path/"source"; source.mkdir(); store=MemoryStore(tmp_path/"state",tmp_path/"kbs")
    store.register_project("p",source,"schema migration fixture")
    task=store.create_task("p","Migrate acceptance","preserve exact criteria",["keep this criterion"])
    dbpath=store._project_database_path("p")
    with sqlite3.connect(dbpath) as db:
        db.execute("DROP TABLE task_acceptance_history")
        db.execute("ALTER TABLE tasks DROP COLUMN acceptance_revision")
        db.execute("PRAGMA user_version=9")
    restored=store.get_task("p",task["id"])
    assert restored["acceptance"]==["keep this criterion"] and restored["acceptance_revision"]==1
    history=store.task_acceptance_history("p",task["id"])["history"]
    assert len(history)==1 and history[0]["criteria"]==["keep this criterion"]
    backups=list((tmp_path/"state"/"migration-backups").glob("project-schema-v9-to-v10-*.sqlite3"))
    assert backups
