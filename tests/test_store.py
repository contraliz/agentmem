from __future__ import annotations

import threading
import time
import multiprocessing
import subprocess
from pathlib import Path

import pytest

from agentmem.store import MemoryStore


def _claim_in_process(data_dir: str, task_id: str, gate, queue, owner: str):
    store=MemoryStore(data_dir)
    gate.wait()
    try: queue.put(("claimed",store.claim_task("p",task_id,owner)["task"]["owner"]))
    except ValueError: queue.put(("rejected",owner))


@pytest.fixture
def setup(tmp_path: Path):
    root=tmp_path/"project"; root.mkdir()
    data=tmp_path/"state"
    store=MemoryStore(data); store.register_project("p",root,"test project")
    return store,root,data


def test_project_isolation_and_resolution(setup, tmp_path):
    store,root,_=setup
    child=root/"sub"; child.mkdir()
    assert store.resolve_project(cwd=child)["id"]=="p"
    other=tmp_path/"other"; other.mkdir()
    store.register_project("other",other)
    (root/"facts.md").write_text("alpha project fact",encoding="utf-8")
    store.index_project("p")
    assert store.search("p","alpha")["results"]
    assert not store.search("other","alpha")["results"]


def test_incremental_index_excludes_secrets_and_symlinks(setup):
    store,root,_=setup
    (root/"guide.md").write_text("alpha useful evidence",encoding="utf-8")
    (root/".env").write_text("alpha SECRET_DO_NOT_INDEX",encoding="utf-8")
    (root/"credentials.json").write_text("alpha credential",encoding="utf-8")
    (root/"node_modules").mkdir(); (root/"node_modules"/"lib.md").write_text("alpha ignored",encoding="utf-8")
    first=store.index_project("p"); second=store.index_project("p")
    assert first["files_changed"]==1
    assert second["files_changed"]==0
    result=store.search("p","alpha")
    assert len(result["results"])==1 and result["results"][0]["citation"].startswith("guide.md#L")
    assert "SECRET_DO_NOT_INDEX" not in str(result)


def test_index_rejects_symlink_escape(setup, tmp_path):
    store,root,_=setup
    outside=tmp_path/"private.md"; outside.write_text("unique external canary",encoding="utf-8")
    link=root/"external.md"
    try: link.symlink_to(outside)
    except OSError: pytest.skip("Windows symlink creation is not enabled for this account")
    store.index_project("p")
    assert not store.search("p","canary")["results"]


def test_index_does_not_traverse_windows_junction(setup, tmp_path):
    store,root,_=setup
    outside=tmp_path/"outside"; outside.mkdir()
    (outside/"private.md").write_text("junction canary",encoding="utf-8")
    junction=root/"external-directory"
    result=subprocess.run(["cmd.exe","/c","mklink","/J",str(junction),str(outside)],capture_output=True,text=True)
    if result.returncode!=0: pytest.skip("Windows junction creation was unavailable")
    store.index_project("p")
    assert not store.search("p","junction canary")["results"]
    with store.connect() as db:
        assert db.execute("SELECT 1 FROM sources WHERE project_id='p' AND relpath LIKE 'external-directory/%'").fetchone() is None


def test_index_scan_limit_does_not_delete_sources_and_chinese_search_works(setup):
    store,root,_=setup
    (root/"a.md").write_text("project memory system",encoding="utf-8")
    (root/"b.md").write_text("项目记忆系统需要可追溯证据",encoding="utf-8")
    store.index_project("p")
    partial=store.index_project("p",max_files=1)
    assert partial["limit_reached"] and partial["files_removed"]==0
    assert store.search("p","可追溯证据")["results"]


def test_metadata_change_check_detects_add_edit_delete_without_reading_content(setup):
    store,root,_=setup
    file=root/"tracked.md"; file.write_text("initial evidence",encoding="utf-8")
    store.index_project("p")
    clean=store.check_project_changes("p")
    assert clean["changed_count"]==clean["new_count"]==clean["deleted_count"]==0
    file.write_text("edited source is longer",encoding="utf-8")
    (root/"new.md").write_text("new source",encoding="utf-8")
    dirty=store.check_project_changes("p")
    assert "tracked.md" in dirty["changed_paths"] and "new.md" in dirty["new_paths"]
    file.unlink()
    removed=store.check_project_changes("p")
    assert "tracked.md" in removed["deleted_paths"]


def test_attach_caps_freshness_paths_for_small_packet_budget(setup):
    import json
    store,root,_=setup
    for n in range(30):
        name=(f"{n:02d}_"+"x"*140+".md")
        (root/name).write_text("small file",encoding="utf-8")
    packet=store.attach("p",max_chars=1000)
    assert len(json.dumps(packet,ensure_ascii=False,separators=(",",":")))<=1000
    assert packet["source_change_summary"]["new_count"]==30 or packet.get("truncated")


def test_schema_v1_migrates_deterministically(tmp_path):
    import sqlite3
    data=tmp_path/"old"; data.mkdir(); db_path=data/"agentmem.sqlite3"
    db=sqlite3.connect(db_path)
    db.execute("CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,root TEXT NOT NULL UNIQUE,description TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL)")
    db.execute("CREATE TABLE sources(id INTEGER PRIMARY KEY,project_id TEXT NOT NULL,relpath TEXT NOT NULL,sha256 TEXT NOT NULL,indexed_at REAL NOT NULL,UNIQUE(project_id,relpath))")
    db.execute("PRAGMA user_version=1"); db.commit(); db.close()
    migrated=MemoryStore(data)
    with migrated.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0]==2
        cols={row[1] for row in conn.execute("PRAGMA table_info(sources)")}
    assert {"mtime_ns","size_bytes"}.issubset(cols)


def test_retrieval_caps_cites_and_detects_index_revision(setup):
    store,root,_=setup
    (root/"notes.md").write_text("\n".join(f"line {n}" for n in range(1,90))+"\nneedle finding",encoding="utf-8")
    store.index_project("p")
    result=store.search("p","needle",limit=99)
    assert len(result["results"])<=12
    assert result["results"][0]["path"]=="notes.md"
    assert result["results"][0]["fresh"] is True
    old_hash=result["results"][0]["sha256"]
    (root/"notes.md").write_text("changed source has no old token",encoding="utf-8")
    stale=store.search("p","needle")
    assert stale["results"] and stale["results"][0]["sha256"]==old_hash
    store.index_project("p")
    assert not store.search("p","needle")["results"]
    assert store.search("p","")["gap"]


def test_dependency_ready_and_cycle_prevention(setup):
    store,_,_=setup
    a=store.create_task("p","first","do first",["check"])
    b=store.create_task("p","second","after first",["check"],depends_on=[a["id"]])
    assert store.get_task("p",a["id"])["ready"] is True
    assert store.get_task("p",b["id"])["ready"] is False
    with pytest.raises(ValueError,match="acyclic"):
        store.update_task("p",a["id"],depends_on=[b["id"]])
    assert store.get_task("p",a["id"])["dependencies"]==[]
    updated=store.update_task("p",b["id"],priority=1,title="renamed")
    assert updated["priority"]==1 and updated["title"]=="renamed"
    with pytest.raises(ValueError,match="another project"):
        store.create_task("p","bad dependency","no",depends_on=["missing"])
    with pytest.raises(ValueError,match="project-relative"):
        store.create_task("p","unsafe path","no",authorized_paths=["../outside"])
    with pytest.raises(ValueError,match="project-relative"):
        store.create_task("p","absolute path","no",authorized_paths=[str(Path("C:/outside"))])


def test_atomic_claim_has_single_owner_and_owner_lease_checks(setup):
    store,_,_=setup; task=store.create_task("p","work","do work",["evidence"])
    barrier=threading.Barrier(2); results=[]
    def claim(name):
        barrier.wait()
        try: results.append(store.claim_task("p",task["id"],name))
        except ValueError as e: results.append(str(e))
    threads=[threading.Thread(target=claim,args=(f"worker{i}",)) for i in range(2)]
    [t.start() for t in threads]; [t.join() for t in threads]
    winners=[r for r in results if isinstance(r,dict)]
    assert len(winners)==1
    winner=winners[0]; owner=winner["task"]["owner"]
    assert "lease_token" not in winner["task"]
    assert "lease_token" not in store.export_project("p")["tasks"][0]
    with pytest.raises(ValueError,match="currently claimed"):
        store.claim_task("p",task["id"],"third")
    with pytest.raises(ValueError,match="does not match"):
        store.update_checkpoint("p",task["id"],"intruder",winner["lease_token"],{})
    assert store.update_checkpoint("p",task["id"],owner,winner["lease_token"],{"last_verified":"state"})["checkpoint"]=={"last_verified":"state"}


def test_atomic_claim_across_processes(setup):
    store,_,data=setup; task=store.create_task("p","shared","prevent duplicate work")
    ctx=multiprocessing.get_context("spawn")
    gate=ctx.Barrier(2); queue=ctx.Queue()
    workers=[ctx.Process(target=_claim_in_process,args=(str(data),task["id"],gate,queue,f"process-{i}")) for i in range(2)]
    [p.start() for p in workers]; [p.join(20) for p in workers]
    assert all(p.exitcode==0 for p in workers)
    results=[queue.get(timeout=2) for _ in workers]
    assert sum(1 for state,_ in results if state=="claimed")==1


def test_expired_lease_reclaim_and_receipt_completion_evidence(setup):
    store,_,_=setup; task=store.create_task("p","work","do work",["accept"])
    c=store.claim_task("p",task["id"],"one",60)
    with store.connect() as db: db.execute("UPDATE tasks SET lease_until=? WHERE id=?",(time.time()-5,task["id"]))
    c2=store.claim_task("p",task["id"],"two",60)
    assert c2["task"]["owner"]=="two"
    bad=store.record_receipt("p",task["id"],"two",c2["lease_token"],{"status":"done","acceptance_checks":[{"criterion":"accept","met":True}]},"k1")
    assert bad["status"]=="in_progress" and not bad["completion_accepted"]
    good=store.record_receipt("p",task["id"],"two",c2["lease_token"],{"status":"done","acceptance_checks":[{"criterion":"accept","met":True,"evidence":"test log"}]},"k2")
    assert good["status"]=="done"
    duplicate=store.record_receipt("p",task["id"],"two","bad",{},"k2")
    assert duplicate["duplicate"] is True


def test_lessons_need_review_and_are_project_scoped(setup, tmp_path):
    store,_,_=setup
    other=tmp_path/"other"; other.mkdir(); store.register_project("q",other)
    lesson=store.propose_lesson("p","Use FTS","search project docs","query lexical index",["notes.md#L2"])
    assert lesson["status"]=="candidate"
    assert not store.search_lessons("p","search")
    store.review_lesson("p",lesson["id"],"accepted")
    assert store.search_lessons("p","search")
    assert not store.search_lessons("q","search")


def test_receipt_tracks_applied_accepted_lesson_and_blocking(setup):
    store,_,_=setup
    lesson=store.propose_lesson("p","Check requirements","before changing API","compare requested behavior with source",["PROJECT.md#L1"])
    store.review_lesson("p",lesson["id"],"accepted")
    task=store.create_task("p","API task","make change",["test evidence"])
    claimed=store.claim_task("p",task["id"],"codex")
    blocked=store.set_blocker("p",task["id"],"codex",claimed["lease_token"],"Waiting for source owner")
    assert blocked["blocked"] and not blocked["ready"]
    with pytest.raises(ValueError,match="blocked"):
        store.claim_task("p",task["id"],"other")
    store.set_blocker("p",task["id"],"codex",claimed["lease_token"],"")
    result=store.record_receipt("p",task["id"],"codex",claimed["lease_token"],
       {"status":"done","lesson_ids":[lesson["id"]],"lesson_feedback":"prevented a mismatch",
        "acceptance_checks":[{"criterion":"test evidence","met":True,"evidence":"test passed"}]},"lesson-run")
    assert result["status"]=="done"
    with store.connect() as db:
        uses=db.execute("SELECT outcome FROM lesson_uses WHERE lesson_id=?",(lesson["id"],)).fetchall()
    assert uses[0]["outcome"]=="prevented a mismatch"


def test_restart_persistence_and_export(setup):
    store,_,data=setup
    task=store.create_task("p","persist","survive restart",["check"])
    restored=MemoryStore(data)
    assert restored.get_task("p",task["id"])["title"]=="persist"
    exported=restored.export_project("p")
    assert exported["schema_version"]==1 and exported["tasks"]
    with restored.connect() as db: assert db.execute("PRAGMA user_version").fetchone()[0]==2


def test_task_context_is_bounded_and_reports_indexed_revision_change(setup):
    store,root,_=setup
    (root/"notes.md").write_text("task source evidence for requirements",encoding="utf-8")
    store.index_project("p")
    task=store.create_task("p","requirements review","verify requirements against docs")
    initial=store.task_context("p",task["id"],max_chars=1000)
    assert initial["indexed_source_revision_changed_since_creation"] is False
    from agentmem.store import _json
    assert len(_json(initial))<=1000
    (root/"notes.md").write_text("task source evidence for changed requirements",encoding="utf-8")
    store.index_project("p")
    refreshed=store.task_context("p",task["id"])
    assert refreshed["indexed_source_revision_changed_since_creation"] is True
