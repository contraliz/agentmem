from __future__ import annotations

import sqlite3
import json
from pathlib import Path

import pytest

from agentmem.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path):
    source=tmp_path/"source"; source.mkdir()
    result=MemoryStore(tmp_path/"state",tmp_path/"kbs")
    result.register_project("p",source,"test project")
    other=tmp_path/"other"; other.mkdir()
    result.register_project("q",other,"other project")
    return result


def make_requirement(store: MemoryStore, project="p"):
    return store.create_requirement(project,"Keep the interface stable","Keep the existing API stable.",
        original_quote="Keep the public API compatible.",criteria=["Existing methods still work."],
        source_type="user_conversation",source_ref="test fixture",idempotency_key=f"req-{project}")


def create_proposal(store: MemoryStore, *, targets=None, approval_required=True, key="proposal-1"):
    return store.create_proposal("p","Controller rollout plan","Update internals; retain the public API.",
        targets=targets or [],approval_required=approval_required,created_by="codex-main",idempotency_key=key)


def approval(store: MemoryStore, proposal, *, action="approved", quote="Yes, approve this exact plan.", key="approval-1"):
    args=dict(expected_revision=proposal["revision"],original_quote=quote,source_type="user_conversation",
        source_ref="current Codex conversation",source_date="unknown",recorded_by="user-supplied wording",idempotency_key=key)
    if action=="approved": return store.approve_proposal("p",proposal["id"],**args)
    if action=="rejected": return store.reject_proposal("p",proposal["id"],**args)
    raise AssertionError(action)


def test_approval_pins_proposal_and_requirement_versions_and_becomes_stale(store):
    req=make_requirement(store)
    proposal=create_proposal(store,targets=[{"kind":"requirement","id":req["id"],"revision":req["revision"]}])
    assert proposal["approval"]["status"]=="not_recorded"
    event=approval(store,proposal)
    assert event["decision"]=="approved" and event["proposal_revision"]==1
    assert event["proposal_snapshot"]["content"]==proposal["content"]
    assert event["target_snapshots"][0]["snapshot"]["statement"]==req["statement"]
    assert store.proposal_status("p",proposal["id"])["status"]=="approved"

    store.update_requirement("p",req["id"],expected_revision=req["revision"],idempotency_key="req-edit",
        statement="Keep the API stable and add no breaking changes.")
    stale=store.proposal_status("p",proposal["id"])
    assert stale["status"]=="stale_target" and stale["stale_targets"]==[f"requirement:{req['id']}"]
    gated=store.create_task("p","Implement rollout","Follow the proposal",acceptance=["implementation verified"],approval_proposal_id=proposal["id"])
    with pytest.raises(ValueError,match="current approval"):
        store.claim_task("p",gated["id"],"worker")

    revised=store.update_proposal("p",proposal["id"],expected_revision=1,idempotency_key="proposal-v2",
        content="Update internals; retain the API and avoid breaking changes.",
        targets=[{"kind":"requirement","id":req["id"],"revision":2}])
    assert revised["approval"]["status"]=="stale_proposal"
    event2=approval(store,revised,quote="Yes, approve the revised plan.",key="approval-2")
    assert event2["proposal_revision"]==2
    assert store.proposal_status("p",proposal["id"])["status"]=="approved"
    assert store.proposal_history("p",proposal["id"])["count"]==2
    assert store.approval_history("p",proposal["id"])["count"]==2
    claimed=store.claim_task("p",gated["id"],"worker")
    store.update_proposal("p",proposal["id"],expected_revision=2,idempotency_key="proposal-v3",content="A materially changed rollout.")
    req_context=store._task_requirements("p",store.get_task("p",gated["id"]),0,100)
    receipt=store.record_receipt("p",gated["id"],"worker",claimed["lease_token"],{
        "status":"done","acceptance_checks":[{"criterion":"implementation verified","met":True,"evidence":"test passed"}],
        "requirements_revision":req_context["requirements_revision"],
        "requirement_checks":[{"requirement_id":r["id"],"revision":r["revision"],"met":True,"evidence":"reviewed"} for r in req_context["applicable_requirements"]]
    },"receipt-after-approval-staled")
    assert receipt["status"]=="in_progress" and "current approval" in receipt["note"]


def test_approval_pins_decision_revision_and_rejects_oversized_target_lists(store):
    decision=store.create_decision("p","Use the local index","Use SQLite FTS5",source_type="user_conversation",
        source_ref="test fixture",original_quote="Use a local index.",idempotency_key="decision-1")
    proposal=create_proposal(store,targets=[{"kind":"decision","id":decision["id"],"revision":decision["revision"]}],key="decision-proposal")
    recorded=approval(store,proposal,key="decision-approval")
    pinned=recorded["target_snapshots"][0]
    assert pinned["kind"]=="decision" and pinned["snapshot"]["choice"]=="Use SQLite FTS5"
    store.update_decision("p",decision["id"],expected_revision=decision["revision"],idempotency_key="decision-edit",choice="Use a different index")
    assert store.proposal_status("p",proposal["id"])["status"]=="stale_target"
    with pytest.raises(ValueError,match="at most 50"):
        store.create_proposal("p","Too many targets","Not a valid proposal",targets=[{"kind":"invalid"}]*51,idempotency_key="too-many")


def test_exact_revision_conflicts_cross_project_and_idempotency(store):
    req=make_requirement(store)
    make_requirement(store,"q")
    foreign=store.create_requirement("q","Another requirement","Only in q",idempotency_key="req-q-2")
    with pytest.raises(ValueError,match="does not belong to this project"):
        store.create_proposal("p","Foreign","This should fail",targets=[{"kind":"requirement","id":foreign["id"],"revision":1}],idempotency_key="foreign")
    with pytest.raises(ValueError,match="revision conflict"):
        create_proposal(store,targets=[{"kind":"requirement","id":req["id"],"revision":99}],key="bad-version")
    p=create_proposal(store,key="idempotent-create")
    assert store.create_proposal("p","Controller rollout plan","Update internals; retain the public API.",
        approval_required=True,created_by="codex-main",idempotency_key="idempotent-create")["id"]==p["id"]
    with pytest.raises(ValueError,match="different proposal content"):
        store.create_proposal("p","Changed title","Update internals; retain the public API.",approval_required=True,
            created_by="codex-main",idempotency_key="idempotent-create")
    with pytest.raises(ValueError,match="revision conflict"):
        store.update_proposal("p",p["id"],expected_revision=9,idempotency_key="bad-update",content="new")
    with pytest.raises(ValueError,match="wording, source_type"):
        store.approve_proposal("p",p["id"],expected_revision=1,original_quote="",source_type="",
            source_ref="",recorded_by="",idempotency_key="missing-provenance")
    event=approval(store,p,key="approval-idempotent")
    duplicate=approval(store,p,key="approval-idempotent")
    assert duplicate["event_id"]==event["event_id"] and duplicate["duplicate"] is True
    with pytest.raises(ValueError,match="different approval content"):
        store.approve_proposal("p",p["id"],expected_revision=1,original_quote="A different phrase",source_type="user_conversation",
            source_ref="current Codex conversation",source_date="unknown",recorded_by="user-supplied wording",idempotency_key="approval-idempotent")


def test_rejection_withdrawal_history_and_optional_task_gate(store):
    proposal=create_proposal(store)
    rejected=approval(store,proposal,action="rejected",quote="No, do not proceed.",key="reject-1")
    assert store.proposal_status("p",proposal["id"])["status"]=="rejected"
    accepted=approval(store,proposal,quote="Actually, yes; use this exact plan.",key="approve-1")
    assert store.proposal_status("p",proposal["id"])["status"]=="approved"
    second=approval(store,proposal,quote="I still approve this exact plan.",key="approve-2")
    with pytest.raises(ValueError,match="latest current approved event"):
        store.withdraw_proposal_approval("p",proposal["id"],accepted["event_id"],expected_revision=1,
            original_quote="Withdraw the older event.",source_type="user_conversation",source_ref="current Codex conversation",
            source_date="unknown",recorded_by="user-supplied wording",idempotency_key="withdraw-old")
    store.withdraw_proposal_approval("p",proposal["id"],second["event_id"],expected_revision=1,
        original_quote="I withdraw that approval.",source_type="user_conversation",source_ref="current Codex conversation",
        source_date="unknown",recorded_by="user-supplied wording",idempotency_key="withdraw-1")
    assert store.proposal_status("p",proposal["id"])["status"]=="withdrawn"
    assert store.approval_history("p",proposal["id"])["count"]==4
    task=store.create_task("p","Optional routine task","No approval is required.")
    assert store.claim_task("p",task["id"],"worker")["task"]["status"]=="in_progress"
    unflagged=store.create_proposal("p","Advisory","No gate by default.",idempotency_key="unflagged")
    linked=store.create_task("p","Advisory task","Uses a non-gated proposal.",approval_proposal_id=unflagged["id"])
    assert store.claim_task("p",linked["id"],"worker-2")["task"]["status"]=="in_progress"


def test_task_context_projection_search_conflict_and_export(store):
    req=make_requirement(store)
    p=create_proposal(store,targets=[{"kind":"requirement","id":req["id"],"revision":1}])
    task=store.create_task("p","Implement rollout","Use the accepted proposal",approval_proposal_id=p["id"])
    packet=store.task_context("p",task["id"],max_chars=12000)
    assert packet["approval"]["status"]=="not_recorded" and packet["approval"]["approval_required"] is True
    approval(store,p)
    assert store.task_context("p",task["id"],max_chars=12000)["approval"]["status"]=="approved"
    assert store.claim_task("p",task["id"],"agent")["task"]["status"]=="in_progress"
    document=Path(store.get_project("p")["memory_root"])/"approvals.md"
    assert "Yes, approve this exact plan." in document.read_text(encoding="utf-8")
    found=store.search("p","Controller rollout plan")
    assert found["results"] and found["results"][0]["citation"].startswith("@memory/approvals.md#L")
    exported=store.export_project("p")
    assert exported["schema_version"]==6 and len(exported["proposals"])==1 and len(exported["approval_events"])==1
    document.write_text(document.read_text(encoding="utf-8")+"manual edit\n",encoding="utf-8")
    with pytest.raises(ValueError,match="Manual edits detected"):
        store.sync_records_projection("p","approvals")
    backup=store.sync_records_projection("p","approvals",force=True)
    assert Path(backup["path"]).read_text(encoding="utf-8").startswith("<!-- Generated from AgentMem SQLite")


def test_task_context_keeps_large_approval_body_out_of_bounded_packet(store):
    proposal=store.create_proposal("p","Long plan","Detailed plan.\n"+("step; "*9000),approval_required=True,idempotency_key="large-proposal")
    event=approval(store,proposal,quote="Approve the exact detailed plan.",key="large-approval")
    task=store.create_task("p","Execute the plan","Follow the approved proposal",approval_proposal_id=proposal["id"])
    packet=store.task_context("p",task["id"],max_chars=12000)
    assert packet["approval"]["status"]=="approved"
    assert packet["approval"]["latest_event"]["full_snapshot_in_history"] is True
    assert "content" not in packet["approval"]["latest_event"]["proposal_snapshot"]
    assert len(json.dumps(packet,ensure_ascii=False,separators=(",",":")))<=12000
    history=store.approval_history("p",proposal["id"])
    assert history["events"][0]["proposal_snapshot"]["content"]==proposal["content"]


def test_v5_database_upgrades_without_losing_task_or_requirement(tmp_path):
    source=tmp_path/"source"; source.mkdir(); data=tmp_path/"state"; memory=tmp_path/"kbs"
    store=MemoryStore(data,memory); store.register_project("p",source,"upgrade test")
    req=make_requirement(store)
    task=store.create_task("p","Existing task","Keep this task")
    dbpath=store._project_database_path("p")
    with sqlite3.connect(dbpath) as db:
        db.execute("DROP TABLE approval_events"); db.execute("DROP TABLE proposal_history"); db.execute("DROP TABLE proposals")
        db.execute("ALTER TABLE tasks DROP COLUMN approval_proposal_id")
        db.execute("ALTER TABLE project_record_revisions DROP COLUMN approvals_revision")
        db.execute("PRAGMA user_version=5")
    assert store.get_task("p",task["id"])["title"]=="Existing task"
    assert store.get_requirement("p",req["id"])["statement"]==req["statement"]
    with store.connect("p") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0]==10
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert {r[1] for r in db.execute("PRAGMA table_info(tasks)")}.__contains__("approval_proposal_id")
    backups=list((tmp_path/"state"/"migration-backups").glob("project-schema-v5-to-v10-*.sqlite3"))
    assert backups
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert backup.execute("SELECT title FROM tasks WHERE id=?",(task["id"],)).fetchone()[0]=="Existing task"


def test_v6_upgrade_recovers_event_snapshot_from_exact_proposal_history(tmp_path):
    source=tmp_path/"source"; source.mkdir(); store=MemoryStore(tmp_path/"state",tmp_path/"kbs")
    store.register_project("p",source,"v6 approval migration")
    proposal=store.create_proposal("p","Version one","Preserve the endpoint.",approval_required=True,idempotency_key="v6-proposal")
    recorded=approval(store,proposal,key="v6-approval")
    dbpath=store._project_database_path("p")
    with sqlite3.connect(dbpath) as db:
        db.execute("ALTER TABLE approval_events DROP COLUMN proposal_snapshot")
        db.execute("PRAGMA user_version=6")
    current=store.get_proposal("p",proposal["id"])
    assert current["approval"]["status"]=="approved"
    with store.connect("p") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0]==10
        migrated=db.execute("SELECT proposal_snapshot FROM approval_events WHERE event_id=?",(recorded["event_id"],)).fetchone()[0]
        assert json.loads(migrated)["content"]==proposal["content"]
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    backups=list((tmp_path/"state"/"migration-backups").glob("project-schema-v6-to-v10-*.sqlite3"))
    assert backups
