from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from agentmem.store import MemoryStore


@pytest.fixture
def setup(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    data = tmp_path / "state"
    store = MemoryStore(data, tmp_path / "kbs")
    store.register_project("p", source, "test")
    other=tmp_path / "other"; other.mkdir()
    store.register_project("q", other, "other")
    return store


def req(store, **kw):
    defaults = {"project_id":"p", "title":"Preserve project isolation", "statement":"Keep each project's records isolated.",
                "scope":{"project_wide":True}, "source_type":"user_conversation", "source_ref":"current conversation",
                "original_quote":"Keep project data isolated.", "idempotency_key":"create-isolation"}
    defaults.update(kw)
    project = defaults.pop("project_id")
    return store.create_requirement(project, **defaults)


def test_fulfillment_does_not_change_applicability_and_history_is_auditable(setup):
    r = req(setup)
    assert r["lifecycle"] == "current" and r["fulfillment"] == "unknown"
    done = setup.update_requirement("p", r["id"], expected_revision=1, idempotency_key="fulfill-1",
                                    fulfillment="fulfilled", fulfillment_evidence=["tests/test_requirements.py#test_fulfillment"])
    assert done["lifecycle"] == "current" and done["fulfillment"] == "fulfilled"
    task = setup.create_task("p", "update memory", "modify project memory")
    packet = setup.task_context("p", task["id"])
    applicable = packet["requirements"]["applicable_requirements"]
    assert applicable[0]["id"] == r["id"] and applicable[0]["fulfilled"] is True
    assert setup.requirement_history("p", r["id"])[-1]["action"] == "updated"
    retired = setup.update_requirement("p", r["id"], expected_revision=2, idempotency_key="withdraw-1", lifecycle="withdrawn")
    assert retired["fulfillment"] == "fulfilled"
    assert not setup.task_context("p", task["id"])["requirements"]["applicable_requirements"]


def test_proposed_interpretation_is_not_mandatory_and_scope_is_respected(setup):
    req(setup, idempotency_key="create-global")
    proposal = req(setup, title="Maybe a UI requirement", lifecycle="proposed",
                   scope={"components":["ui"]}, idempotency_key="create-ui-proposal")
    other_task = setup.create_task("p", "database change", "modify schema", authorized_paths=["src/store.py"])
    other = setup.task_context("p", other_task["id"])["requirements"]
    assert {r["title"] for r in other["applicable_requirements"]} == {"Preserve project isolation"}
    assert proposal["id"] not in other["proposed_requirement_ids"]
    ui_task = setup.create_task("p", "UI change", "update page", components=["UI"])
    packet = setup.task_context("p", ui_task["id"])["requirements"]
    assert proposal["id"] in packet["proposed_requirement_ids"]
    assert all(x["lifecycle"] == "current" for x in packet["applicable_requirements"])


def test_task_completion_requires_current_requirement_evidence_and_does_not_fulfill_it(setup):
    requirement = req(setup)
    task = setup.create_task("p", "change memory", "preserve separation", ["tests pass"], requirement_ids=[requirement["id"]])
    claim = setup.claim_task("p", task["id"], "codex")
    packet = setup.task_context("p", task["id"])["requirements"]
    good = {"status":"done", "requirements_revision":packet["requirements_revision"],
            "requirement_checks":[{"requirement_id":requirement["id"],"revision":1,"met":True,"evidence":"test passed"}],
            "acceptance_checks":[{"criterion":"tests pass","met":True,"evidence":"pytest passed"}]}
    missing = dict(good); missing.pop("requirement_checks")
    denied = setup.record_receipt("p",task["id"],"codex",claim["lease_token"],missing,"missing-req-check")
    assert denied["status"] == "in_progress" and not denied["completion_accepted"]
    setup.update_requirement("p",requirement["id"],expected_revision=1,idempotency_key="revise-before-receipt",rationale="Updated constraint wording")
    stale=setup.record_receipt("p",task["id"],"codex",claim["lease_token"],good,"stale-req-check")
    assert stale["status"]=="in_progress" and not stale["completion_accepted"]
    fresh_packet=setup.task_context("p",task["id"])["requirements"]
    good["requirements_revision"]=fresh_packet["requirements_revision"]
    good["requirement_checks"][0]["revision"]=fresh_packet["applicable_requirements"][0]["revision"]
    accepted = setup.record_receipt("p",task["id"],"codex",claim["lease_token"],good,"good-req-check")
    assert accepted["status"] == "done"
    assert setup.get_requirement("p",requirement["id"])["fulfillment"] == "unknown"


def test_old_style_completion_without_requirement_revision_fails_closed(setup):
    setup.create_requirement("p","Future UI constraint","Keep UI accessible.",scope={"components":["ui"]},idempotency_key="ui-constraint")
    task=setup.create_task("p","Database cleanup","adjust storage only",["tests pass"],components=["storage"])
    claim=setup.claim_task("p",task["id"],"codex")
    blocked=setup.record_receipt("p",task["id"],"codex",claim["lease_token"],
        {"status":"done","acceptance_checks":[{"criterion":"tests pass","met":True,"evidence":"pytest passed"}]},"old-style")
    assert blocked["status"]=="in_progress" and not blocked["completion_accepted"]
    with setup.connect("p") as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO receipts(id,task_id,project_id,owner,body,created_at,idempotency_key) VALUES('stale',?,'p','old-server','{}',1,'stale')",(task["id"],))
        with pytest.raises(sqlite3.IntegrityError,match="requirements_revision"):
            db.execute("UPDATE tasks SET status='done' WHERE id=?",(task["id"],))
        db.rollback()
    fresh=setup.task_context("p",task["id"])["requirements"]
    final=setup.record_receipt("p",task["id"],"codex",claim["lease_token"],
        {"status":"done","requirements_revision":fresh["requirements_revision"],
         "acceptance_checks":[{"criterion":"tests pass","met":True,"evidence":"pytest passed"}]},"current-revision")
    assert final["status"]=="done"


def test_requirement_scope_pagination_and_stale_links_are_explicit(setup):
    first=req(setup)
    second=req(setup,title="Keep task resumes safe",statement="Save verified state before stopping.",idempotency_key="second",
               scope={"components":["storage"],"condition":"when resuming work","condition_state":"unknown"})
    task=setup.create_task("p","storage work","resume a storage task",authorized_paths=["src/storage.py"],components=["storage"],requirement_ids=[first["id"]])
    packet=setup.task_context("p",task["id"],requirements_limit=1)["requirements"]
    assert packet["applicable_count"]==2 and not packet["requirements_complete"] and packet["next_requirement_offset"]==1
    more=setup.task_context("p",task["id"],requirement_offset=packet["next_requirement_offset"],requirements_limit=1)["requirements"]
    assert more["requirements_complete"]
    assert second["id"] in more["condition_review_ids"] or second["id"] in packet["condition_review_ids"]
    setup.update_requirement("p",first["id"],expected_revision=1,idempotency_key="req-new-revision",rationale="clarified")
    refreshed=setup.task_context("p",task["id"])["requirements"]
    assert first["id"] in refreshed["linked_requirement_revisions_stale"]


def test_requirement_update_revision_conflict_and_idempotency(setup):
    r = req(setup)
    assert req(setup)["id"] == r["id"]
    changed = setup.update_requirement("p",r["id"],expected_revision=1,idempotency_key="rev2",rationale="Clear boundary")
    assert changed["revision"] == 2
    assert setup.update_requirement("p",r["id"],expected_revision=1,idempotency_key="rev2",rationale="Clear boundary")["revision"] == 2
    with pytest.raises(ValueError,match="revision conflict"):
        setup.update_requirement("p",r["id"],expected_revision=1,idempotency_key="rev3",rationale="Changed again")
    with pytest.raises(ValueError,match="different"):
        setup.update_requirement("p",r["id"],expected_revision=2,idempotency_key="rev2",rationale="Reused key")


def test_supersession_requires_a_current_replacement_and_keeps_history(setup):
    old=req(setup)
    with pytest.raises(ValueError,match="Create a current replacement"):
        setup.update_requirement("p",old["id"],expected_revision=1,idempotency_key="bad-supersede",lifecycle="superseded")
    new=setup.create_requirement("p","New isolation boundary","Keep project records separate and retain source roots.",scope={"project_wide":True},
                                 supersedes_id=old["id"],idempotency_key="replacement")
    updated=setup.update_requirement("p",old["id"],expected_revision=1,idempotency_key="mark-superseded",lifecycle="superseded")
    assert updated["lifecycle"]=="superseded" and new["supersedes_id"]==old["id"]
    task=setup.create_task("p","continue","inspect the rule")
    current=[x["id"] for x in setup.task_context("p",task["id"])["requirements"]["applicable_requirements"]]
    assert current==[new["id"]]
    assert setup.requirement_history("p",old["id"])[-1]["snapshot"]["lifecycle"]=="superseded"


def test_superseding_scoped_requirement_inherits_explicit_task_link(setup):
    old=req(setup,scope={"components":["storage"]})
    task=setup.create_task("p","storage task","change storage",components=["storage"],requirement_ids=[old["id"]])
    new=setup.create_requirement("p","Updated storage rule","Retain existing storage layout.",scope={"components":["storage"]},supersedes_id=old["id"],idempotency_key="storage-v2")
    setup.update_requirement("p",old["id"],expected_revision=1,idempotency_key="storage-retire",lifecycle="superseded")
    packet=setup.task_context("p",task["id"])["requirements"]
    current=packet["applicable_requirements"]
    assert len(current)==1 and current[0]["id"]==new["id"]
    assert current[0]["inherited_from_linked_requirement"]==old["id"]


def test_records_project_isolation_decisions_export_and_restart(setup):
    r = req(setup)
    d = setup.create_decision("p","SQLite is local authority","Store structured state in a per-project SQLite DB",rationale="Transactional updates",requirement_ids=[r["id"]],
                              original_quote="The DB is authoritative.",source_type="user_conversation",source_ref="current conversation",idempotency_key="decision-1")
    with pytest.raises(ValueError,match="not found"):
        setup.get_requirement("q",r["id"])
    assert setup.get_decision("p",d["id"])["requirement_ids"] == [r["id"]]
    exported=setup.export_project("p")
    assert exported["requirements"][0]["id"] == r["id"] and exported["decisions"][0]["id"] == d["id"]
    restarted=MemoryStore(setup.data_dir,setup.memory_root)
    assert restarted.get_decision("p",d["id"])["choice"].startswith("Store structured")


def test_schema_v3_upgrade_adds_requirement_tables_and_completion_guard_without_losing_task_state(setup):
    task=setup.create_task("p","existing work","keep this across schema migration")
    db_path=Path(setup.get_project("p")["memory_root"])/"agentmem.sqlite3"
    with setup.connect("p") as db:
        for table in ("decision_requirements","task_requirement_links","decision_history","requirement_history","knowledge_projections","decision_history","decisions","requirements","project_record_revisions","record_counters"):
            db.execute(f"DROP TABLE IF EXISTS {table}")
        db.execute("PRAGMA user_version=3")
    assert setup.get_task("p",task["id"])["title"]=="existing work"
    with setup.connect("p") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0]==10
        assert {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {"requirements","decisions","requirement_history","decision_history"}
        assert db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='guard_task_done_requires_current_requirement_revision'").fetchone()
    assert setup.get_task("p",task["id"])["title"]=="existing work"


def test_decision_revision_and_supersession_are_project_scoped(setup):
    first=setup.create_decision("p","Use SQLite","SQLite is the authoritative structured store.",status="current",idempotency_key="d1")
    updated=setup.update_decision("p",first["id"],expected_revision=1,idempotency_key="d1-edit",rationale="Single-user local MVP")
    assert updated["revision"]==2
    second=setup.create_decision("p","Revisit storage","Keep SQLite until multi-host writes are needed.",status="current",supersedes_id=first["id"],idempotency_key="d2")
    retired=setup.update_decision("p",first["id"],expected_revision=2,idempotency_key="d1-retire",status="superseded")
    assert retired["status"]=="superseded" and second["supersedes_id"]==first["id"]
    assert [d["id"] for d in setup.list_decisions("p","current")] == [second["id"]]
    with pytest.raises(ValueError,match="not found"):
        setup.decision_history("q",first["id"])
    assert [x["revision"] for x in setup.decision_history("p",first["id"])]==[1,2,3]


def test_generated_projection_detects_manual_edit_and_force_backs_up(setup):
    r=req(setup)
    project=setup.get_project("p")
    path=Path(project["memory_root"])/"requirements.md"
    path.write_text("manual note not in database",encoding="utf-8")
    with pytest.raises(ValueError,match="Manual edits"):
        setup.sync_records_projection("p","requirements")
    result=setup.sync_records_projection("p","requirements",force=True)
    assert r["id"] in path.read_text(encoding="utf-8")
    backups=list(path.parent.glob("requirements.md.pre-sync-*.bak"))
    assert backups and "manual note" in backups[0].read_text(encoding="utf-8")


def test_revision_race_only_one_update_wins(setup):
    r=req(setup); barrier=threading.Barrier(2); outcomes=[]
    def change(note):
        barrier.wait()
        try: outcomes.append(setup.update_requirement("p",r["id"],expected_revision=1,idempotency_key="race-"+note,rationale=note)["revision"])
        except ValueError as exc: outcomes.append(str(exc))
    threads=[threading.Thread(target=change,args=(x,)) for x in ("a","b")]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert sum(isinstance(x,int) for x in outcomes)==1
    assert sum(isinstance(x,str) and "revision conflict" in x for x in outcomes)==1


def test_attach_manifest_hidden_does_not_claim_index_empty(setup):
    root=Path(setup.get_project("p")["root"])
    (root/"guide.md").write_text("There is useful project information.",encoding="utf-8")
    setup.index_project("p")
    card=setup.attach("p")
    assert card["indexed_source_count"] >= 1
    assert "No source files have been indexed yet." not in card["unknowns"]


def test_attach_includes_versioned_requirements_and_decision_summary(setup):
    r=req(setup)
    d=setup.create_decision("p","Use project IDs","Every call names a project ID",rationale="Avoid cross-project leakage",requirement_ids=[r["id"]],idempotency_key="attach-decision")
    card=setup.attach("p",max_chars=10000)
    summary=card["requirements"]
    assert summary["revision"]==1 and summary["current_count"]==1
    assert summary["project_wide"][0]["id"]==r["id"]
    assert summary["current_decisions"][0]["id"]==d["id"]
    tiny=setup.attach("p",max_chars=1000)
    assert len(json.dumps(tiny,ensure_ascii=False,separators=(",",":")))<=1000
    if tiny.get("truncated"):
        assert tiny["requirements"]["current_count"]==1
