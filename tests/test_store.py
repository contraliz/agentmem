from __future__ import annotations

import threading
import time
import multiprocessing
import json
import subprocess
from pathlib import Path

import pytest

from agentmem.store import MemoryStore


def _claim_in_process(data_dir: str, task_id: str, gate, queue, owner: str):
    store=MemoryStore(data_dir)
    gate.wait()
    try: queue.put(("claimed",store.claim_task("p",task_id,owner)["task"]["owner"]))
    except ValueError: queue.put(("rejected",owner))


def _initialize_migrated_store(data_dir: str, memory_root: str, gate, queue):
    gate.wait()
    try:
        store=MemoryStore(data_dir,memory_root)
        queue.put(("ok",len(store.list_projects())))
    except Exception as exc:
        queue.put(("error",type(exc).__name__+":"+str(exc)))


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
    # A newly registered project includes safe, intentionally empty KB scaffolds.
    assert first["files_changed"]==9
    assert second["files_changed"]==0
    result=store.search("p","alpha")
    assert len(result["results"])==1 and result["results"][0]["citation"].startswith("guide.md#L")
    assert "SECRET_DO_NOT_INDEX" not in str(result)


def test_generated_egg_info_is_excluded_from_index_and_freshness(setup):
    store,root,_=setup
    generated=root/"src"/"sample.egg-info"
    generated.mkdir(parents=True)
    (generated/"SOURCES.txt").write_text("EGG_INFO_SECRET_CANARY_ZZ",encoding="utf-8")
    (root/"guide.md").write_text("the project answer is grounded in this guide",encoding="utf-8")
    store.index_project("p")
    assert store.search("p","EGG_INFO_SECRET_CANARY_ZZ")["results"] == []
    assert not any(".egg-info" in path for path in store.check_project_changes("p")["new_paths"])


def test_attach_can_bundle_focused_cited_question_evidence(setup):
    store,root,_=setup
    (root/"guide.md").write_text("The retry limit is four attempts.\n" + "detail " * 400,encoding="utf-8")
    store.index_project("p")
    context=store.attach("p",query="What is the retry limit?")
    evidence=context["question_context"]
    assert evidence["query"]=="What is the retry limit?"
    assert evidence["results"][0]["citation"].startswith("guide.md#L1-")
    assert evidence["results"][0]["fresh"] is True
    assert len(evidence["results"][0]["excerpt"])<=1200
    assert evidence["results"][0]["excerpt_truncated"] is True
    plain=store.attach("p")
    assert "question_context" not in plain
    assert plain["indexed_sources"]==[] and plain["indexed_source_count"]==9
    assert plain["source_manifest_included"] is False
    detailed=store.attach("p",include_sources=True)
    assert {item["path"] for item in detailed["indexed_sources"]}=={
        "guide.md","@memory/PROJECT.md","@memory/requirements.md","@memory/decisions.md",
        *{f"@memory/knowledge/{page_id}.md" for page_id in ("purpose","architecture","risks","history","materials")},
    }
    compact=store.attach("p",max_chars=1000,query="retry limit")
    assert len(json.dumps(compact,ensure_ascii=False,separators=(",",":")))<=1000
    if compact.get("question_context",{}).get("results"):
        assert compact["question_context"]["results"][0]["citation"].startswith("guide.md#L")
        assert compact["question_context"]["results"][0]["excerpt_truncated"] is True


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
    with store.connect("p") as db:
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
    assert packet["source_change_summary"]["new_count"]==31 or packet.get("truncated")


def test_schema_v1_migrates_deterministically(tmp_path):
    import sqlite3
    data=tmp_path/"old"; data.mkdir(); db_path=data/"agentmem.sqlite3"
    db=sqlite3.connect(db_path)
    db.execute("CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,root TEXT NOT NULL UNIQUE,description TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL)")
    db.execute("CREATE TABLE sources(id INTEGER PRIMARY KEY,project_id TEXT NOT NULL,relpath TEXT NOT NULL,sha256 TEXT NOT NULL,indexed_at REAL NOT NULL,UNIQUE(project_id,relpath))")
    db.execute("PRAGMA user_version=1"); db.commit(); db.close()
    migrated=MemoryStore(data)
    assert migrated.list_projects()==[]
    assert migrated.db_path.exists()
    with migrated.connect() as conn:
        assert "memory_root" in {row[1] for row in conn.execute("PRAGMA table_info(projects)")}


def test_shared_legacy_store_migrates_all_records_into_isolated_project_directories(tmp_path):
    import hashlib
    import json
    import sqlite3

    data=tmp_path/"legacy-state"; data.mkdir()
    memory=tmp_path/"projects"
    alpha=tmp_path/"alpha"; alpha.mkdir()
    beta=tmp_path/"beta"; beta.mkdir()
    old_brief=alpha/".agentmem"/"PROJECT.md"; old_brief.parent.mkdir()
    old_brief.write_text("# Alpha manager brief\nThe old test migration evidence stays verifiable.\n",encoding="utf-8")
    legacy_db=data/"agentmem.sqlite3"
    builder=MemoryStore(data/"builder",memory_root=tmp_path/"builder-kbs")
    builder._create_project_database(legacy_db)
    now=time.time(); source_text=old_brief.read_text(encoding="utf-8"); digest=hashlib.sha256(source_text.encode()).hexdigest()
    db=sqlite3.connect(legacy_db)
    db.execute("INSERT INTO projects VALUES(?,?,?,?,?)",("alpha","alpha",str(alpha),"alpha desc",now))
    db.execute("INSERT INTO projects VALUES(?,?,?,?,?)",("beta","beta",str(beta),"beta desc",now))
    db.execute("INSERT INTO sources(project_id,relpath,sha256,indexed_at,mtime_ns,size_bytes) VALUES(?,?,?,?,?,?)",("alpha",".agentmem/PROJECT.md",digest,now,old_brief.stat().st_mtime_ns,old_brief.stat().st_size))
    db.execute("INSERT INTO chunks(project_id,relpath,sha256,line_start,line_end,body) VALUES(?,?,?,?,?,?)",("alpha",".agentmem/PROJECT.md",digest,1,2,source_text.strip()))
    db.execute("INSERT INTO tasks(id,project_id,title,goal,priority,status,acceptance,authorized_paths,owner,lease_token,lease_until,source_revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",("a1","alpha","First","Preserve evidence",1,"done","[\"verified\"]","[]","","",0,"old-revision",now,now))
    db.execute("INSERT INTO tasks(id,project_id,title,goal,priority,status,acceptance,authorized_paths,owner,lease_token,lease_until,source_revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",("a2","alpha","Second","Remain queued",2,"open","[]","[]","","",0,"old-revision",now,now))
    db.execute("INSERT INTO dependencies(task_id,depends_on) VALUES(?,?)",("a2","a1"))
    receipt_body=json.dumps({"status":"done","acceptance_checks":[{"criterion":"verified","met":True,"evidence":"test evidence"}]})
    db.execute("INSERT INTO receipts VALUES(?,?,?,?,?,?,?)",("r1","a1","alpha","tester",receipt_body,now,"idem"))
    db.execute("INSERT INTO lessons VALUES(?,?,?,?,?,?,?,?,?,?)",("l1","alpha","Use evidence","before answering","cite the verified project brief","[\"@memory/PROJECT.md#L1-L2\"]","preserved","accepted","",now))
    db.execute("INSERT INTO lesson_uses VALUES(?,?,?,?)",("l1","r1","helped",now))
    db.execute("INSERT INTO tasks(id,project_id,title,goal,priority,status,acceptance,authorized_paths,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",("b1","beta","Isolated","Keep separate",3,"open","[]","[]",now,now))
    db.commit(); db.close()

    store=MemoryStore(data,memory)
    projects={p["id"]:p for p in store.list_projects()}
    assert set(projects)=={"alpha","beta"}
    assert Path(projects["alpha"]["memory_root"])==memory/"alpha"
    assert Path(projects["beta"]["root"])==beta
    assert (memory/"alpha"/"PROJECT.md").read_text(encoding="utf-8")==old_brief.read_text(encoding="utf-8")
    assert not (memory/"beta"/"PROJECT.md").read_text(encoding="utf-8").startswith("# Alpha")
    assert store.get_task("alpha","a2")["dependencies"]==["a1"]
    assert store.get_task("beta","b1")["title"]=="Isolated"
    assert store.export_project("alpha")["receipts"][0]["id"]=="r1"
    assert store.export_project("alpha")["lesson_uses"][0]["outcome"]=="helped"
    cited=store.search("alpha","old test migration evidence")
    assert cited["results"][0]["citation"].startswith("@memory/PROJECT.md#L1-")
    assert cited["results"][0]["fresh"] is True
    assert store.search("beta","old test migration evidence")["results"]==[]
    with store.connect("alpha") as a_db, store.connect("beta") as b_db:
        assert a_db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]==2
        assert b_db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]==1
        assert a_db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert b_db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    backup_dir=data/"migration-backups"
    backups=list(backup_dir.glob("legacy-*.sqlite3"))
    assert backups
    assert not Path(str(backups[0])+"-wal").exists()
    restored=MemoryStore(data,memory)
    assert restored.get_task("alpha","a2")["id"]=="a2"
    with sqlite3.connect(legacy_db) as frozen:
        with pytest.raises(sqlite3.IntegrityError,match="migrated"):
            frozen.execute("UPDATE tasks SET title='wrong' WHERE id='a1'")
        with pytest.raises(sqlite3.IntegrityError,match="migrated"):
            frozen.execute("INSERT INTO receipts VALUES(?,?,?,?,?,?,?)",("r-old","a1","alpha","tester","{}",now,"old"))
        with pytest.raises(sqlite3.IntegrityError,match="migrated"):
            frozen.execute("INSERT INTO lessons VALUES(?,?,?,?,?,?,?,?,?,?)",("l-old","alpha","old","trigger","procedure","[]","","candidate","",now))
        # FTS5 virtual tables cannot have SQLite triggers. An old index operation
        # must fail on its guarded source-table write and roll back the FTS change
        # in the same transaction.
        old_chunk_count=frozen.execute("SELECT COUNT(*) FROM chunks WHERE project_id='alpha'").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError,match="migrated"):
            with frozen:
                frozen.execute("INSERT INTO chunks(project_id,relpath,sha256,line_start,line_end,body) VALUES(?,?,?,?,?,?)",("alpha","__old-process-test__","test",1,1,"temporary"))
                frozen.execute("INSERT INTO sources(project_id,relpath,sha256,indexed_at,mtime_ns,size_bytes) VALUES(?,?,?,?,?,?)",("alpha","__old-process-test__","test",now,0,0))
        assert frozen.execute("SELECT COUNT(*) FROM chunks WHERE project_id='alpha'").fetchone()[0]==old_chunk_count
    assert (alpha/".agentmem"/"PROJECT.md").read_text(encoding="utf-8")=="# Alpha manager brief\nThe old test migration evidence stays verifiable.\n"
    # The new active project database remains fully writable after the legacy
    # store has been frozen.
    active_task=store.create_task("alpha","Post-migration write","Verify active store writes",["receipt saved"])
    claim=store.claim_task("alpha",active_task["id"],"migration-test")
    result=store.record_receipt("alpha",active_task["id"],"migration-test",claim["lease_token"],{
        "status":"done","changed_artifacts":["active project DB"],"checks":["write"],"remaining_work":[],
        "acceptance_checks":[{"criterion":"receipt saved","met":True,"evidence":"new active DB accepted task receipt"}],
    },"active-store-write")
    assert result["status"]=="done"


def test_concurrent_first_start_serializes_legacy_migration(tmp_path):
    import sqlite3
    data=tmp_path/"state"; data.mkdir(); memory=tmp_path/"projects"
    source=tmp_path/"source"; source.mkdir()
    builder=MemoryStore(data/"schema",memory_root=tmp_path/"schema-kbs")
    legacy=data/"agentmem.sqlite3"; builder._create_project_database(legacy)
    with sqlite3.connect(legacy) as db:
        db.execute("INSERT INTO projects VALUES(?,?,?,?,?)",("p","p",str(source),"parallel migration",time.time()))
    ctx=multiprocessing.get_context("spawn"); gate=ctx.Event(); queue=ctx.Queue()
    workers=[ctx.Process(target=_initialize_migrated_store,args=(str(data),str(memory),gate,queue)) for _ in range(2)]
    [p.start() for p in workers]; gate.set(); [p.join(30) for p in workers]
    assert all(p.exitcode==0 for p in workers)
    results=[queue.get(timeout=2) for _ in workers]
    assert sorted(results)==[("ok",1),("ok",1)]
    store=MemoryStore(data,memory)
    assert [p["id"] for p in store.list_projects()]==["p"]
    with store.connect("p") as db: assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"


def test_project_brief_is_written_only_to_external_knowledge_directory(setup):
    store,root,_=setup
    old=root/".agentmem"/"PROJECT.md"; old.parent.mkdir(); old.write_text("old source copy",encoding="utf-8")
    store.index_project("p")
    result=store.update_project_brief("p","# Updated\nExternal project knowledge only.\n")
    assert Path(result["path"])==store.memory_root/"p"/"PROJECT.md"
    assert Path(result["path"]).read_text(encoding="utf-8").startswith("# Updated")
    assert old.read_text(encoding="utf-8")=="old source copy"
    assert result["indexed"] is True
    assert "@memory/PROJECT.md" not in store.check_project_changes("p")["changed_paths"]
    hit=store.search("p","External project knowledge")["results"][0]
    assert hit["citation"].startswith("@memory/PROJECT.md#L") and hit["fresh"] is True


def test_project_memory_paths_reject_source_overlap_and_implicit_rebind(tmp_path):
    data=tmp_path/"state"; store=MemoryStore(data,memory_root=tmp_path/"kbs")
    source=tmp_path/"source"; source.mkdir()
    with pytest.raises(ValueError,match="separate"):
        store.register_project("bad",source,memory_root=source/".agentmem")
    store.register_project("safe",source)
    with pytest.raises(ValueError,match="already bound"):
        store.register_project("safe",source,memory_root=tmp_path/"other-memory")
    custom_source=tmp_path/"custom-source"; custom_source.mkdir()
    custom=store.register_project("custom",custom_source,memory_root=tmp_path/"external-kb"/"custom")
    assert Path(custom["memory_root"])==tmp_path/"external-kb"/"custom"
    overlapping_source=tmp_path/"another-source"; overlapping_source.mkdir()
    with pytest.raises(ValueError,match="overlaps another project's knowledge base"):
        store.register_project("overlap",overlapping_source,memory_root=tmp_path/"external-kb")


def test_project_store_refuses_a_mismatched_knowledge_manifest(setup):
    store,_,_=setup
    manifest=store.memory_root/"p"/".agentmem-project.json"
    original=manifest.read_text(encoding="utf-8")
    manifest.write_text('{"project_id":"other","source_root":"wrong","format":1}',encoding="utf-8")
    try:
        with pytest.raises(ValueError,match="manifest does not match"):
            store.connect("p")
    finally:
        manifest.write_text(original,encoding="utf-8")


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
    with store.connect("p") as db: db.execute("UPDATE tasks SET lease_until=? WHERE id=?",(time.time()-5,task["id"]))
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
    with store.connect("p") as db:
        uses=db.execute("SELECT outcome FROM lesson_uses WHERE lesson_id=?",(lesson["id"],)).fetchall()
    assert uses[0]["outcome"]=="prevented a mismatch"


def test_restart_persistence_and_export(setup):
    store,_,data=setup
    task=store.create_task("p","persist","survive restart",["check"])
    restored=MemoryStore(data)
    assert restored.get_task("p",task["id"])["title"]=="persist"
    exported=restored.export_project("p")
    assert exported["schema_version"]==3 and exported["tasks"]
    assert exported["knowledge_manifest"]["page_count"]==5
    with restored.connect("p") as db: assert db.execute("PRAGMA user_version").fetchone()[0]==5


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
