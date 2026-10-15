from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agentmem.store import KNOWLEDGE_PAGE_TEMPLATES, MemoryStore


@pytest.fixture
def setup(tmp_path: Path):
    source=tmp_path/"source"; source.mkdir()
    data=tmp_path/"state"
    store=MemoryStore(data,tmp_path/"kbs")
    store.register_project("p",source,"isolated project")
    other=tmp_path/"other"; other.mkdir()
    store.register_project("q",other,"second project")
    return store,source


def test_new_registration_creates_empty_topic_scaffold_in_its_own_kb(setup):
    store,source=setup
    project=store.get_project("p")
    kb=Path(project["memory_root"])
    pages=store.list_knowledge_pages("p")
    assert pages["count"]==len(KNOWLEDGE_PAGE_TEMPLATES)
    assert {p["page_id"] for p in pages["pages"]}==set(KNOWLEDGE_PAGE_TEMPLATES)
    assert all("Not documented yet." in (kb/"knowledge"/f"{page_id}.md").read_text(encoding="utf-8") for page_id in KNOWLEDGE_PAGE_TEMPLATES)
    brief=(kb/"PROJECT.md").read_text(encoding="utf-8")
    assert "knowledge/architecture.md" in brief and "No project-specific facts have been verified yet." in brief
    assert (kb/"requirements.md").exists() and (kb/"decisions.md").exists()
    assert not (source/"knowledge").exists()
    assert all(p["path"].startswith("@memory/knowledge/") for p in pages["pages"])


def test_init_fills_only_missing_pages_without_changing_existing_pages_or_brief(setup):
    store,_=setup
    kb=Path(store.get_project("p")["memory_root"])
    purpose=kb/"knowledge"/"purpose.md"
    purpose.write_text("# Custom purpose\n\nVerified user intent.\n",encoding="utf-8")
    (kb/"knowledge"/"risks.md").unlink()
    old_brief=(kb/"PROJECT.md").read_text(encoding="utf-8")
    result=store.initialize_knowledge("p")
    assert result["created_pages"]==["risks"]
    assert result["default_pages_already_present"]==len(KNOWLEDGE_PAGE_TEMPLATES)-1
    assert purpose.read_text(encoding="utf-8")=="# Custom purpose\n\nVerified user intent.\n"
    assert (kb/"knowledge"/"risks.md").exists()
    assert (kb/"PROJECT.md").read_text(encoding="utf-8")==old_brief
    assert result["brief_has_navigation"] is True
    assert next(p for p in result["pages"] if p["page_id"]=="risks")["fresh"] is True


def test_explicit_init_for_legacy_project_preserves_its_existing_brief(setup):
    store,_=setup
    kb=Path(store.get_project("p")["memory_root"])
    brief=kb/"PROJECT.md"
    brief.write_text("# Existing curated brief\n\nPreserve this user-authored text.\n",encoding="utf-8")
    directory=kb/"knowledge"
    for page_id in KNOWLEDGE_PAGE_TEMPLATES:
        (directory/f"{page_id}.md").unlink()
    directory.rmdir()
    result=store.initialize_knowledge("p")
    assert set(result["created_pages"])==set(KNOWLEDGE_PAGE_TEMPLATES)
    assert brief.read_text(encoding="utf-8")=="# Existing curated brief\n\nPreserve this user-authored text.\n"
    assert result["brief_has_navigation"] is False


def test_create_update_history_hash_check_and_immediate_search(setup):
    store,_=setup
    page=store.create_knowledge_page("p","interface-notes","Interface notes","The service accepts a signed session token.")
    assert page["fresh"] is True and page["path"]=="@memory/knowledge/interface-notes.md"
    first=store.search("p","signed session token")["results"][0]
    assert first["citation"].startswith("@memory/knowledge/interface-notes.md#L1-") and first["fresh"] is True
    before=store.read_knowledge_page("p","interface-notes")
    updated=store.update_knowledge_page("p","interface-notes","# Interface notes\n\nThe service accepts a signed session token with a two-minute expiry.\n",before["sha256"])
    assert updated["updated"] is True and updated["fresh"] is True
    assert store.search("p","two minute expiry")["results"][0]["fresh"] is True
    history=store.knowledge_page_history("p","interface-notes")
    assert len(history["history"])==1 and history["history"][0]["sha256"]==before["sha256"]
    previous=store.read_knowledge_page("p","interface-notes",history_id=history["history"][0]["history_id"])
    assert "signed session token." in previous["content"] and "two-minute" not in previous["content"]
    with pytest.raises(ValueError,match="changed since it was read"):
        store.update_knowledge_page("p","interface-notes","# Stale change\n",before["sha256"])
    with pytest.raises(ValueError,match="already exists"):
        store.create_knowledge_page("p","interface-notes","Duplicate","Should not replace the original")


def test_manual_edit_shows_stale_until_indexed_and_history_is_not_indexed(setup):
    store,_=setup
    store.index_project("p")
    path=Path(store.get_project("p")["memory_root"])/"knowledge"/"architecture.md"
    path.write_text("# Architecture\n\nManual addition about transactional snapshots.\n",encoding="utf-8")
    listed=store.list_knowledge_pages("p")
    assert not next(p for p in listed["pages"] if p["page_id"]=="architecture")["fresh"]
    changes=store.check_project_changes("p")
    assert "@memory/knowledge/architecture.md" in changes["changed_paths"]
    store.index_project("p")
    hit=store.search("p","transactional snapshots")["results"][0]
    assert hit["path"]=="@memory/knowledge/architecture.md" and hit["fresh"] is True
    assert store.list_knowledge_pages("p")["unindexed_count"]==0
    store.update_knowledge_page("p","architecture","# Architecture\n\nUpdated transactional snapshot notes.\n",hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest())
    store.index_project("p")
    with store.connect("p") as db:
        indexed=[r[0] for r in db.execute("SELECT relpath FROM sources WHERE project_id='p'")]
    assert not any(".history" in rel for rel in indexed)


def test_page_evidence_and_mandatory_requirements_coexist_in_task_context(setup):
    store,_=setup
    page=store.create_knowledge_page("p","storage-policy","Storage policy","Use transactional snapshots before changing persisted records.")
    requirement=store.create_requirement("p","Protect persisted state","Changes to persisted records must preserve data and history.",scope={"project_wide":True},idempotency_key="storage-rule")
    task=store.create_task("p","Update storage records","preserve persisted records and history",["tests pass"])
    packet=store.task_context("p",task["id"])
    assert any(r["id"]==requirement["id"] for r in packet["requirements"]["applicable_requirements"])
    assert any(e["path"]==page["path"] for e in packet["evidence"])
    assert packet["requirements"]["requirements_complete"]


def test_pages_are_isolated_paged_and_output_is_bounded(setup):
    store,_=setup
    p=store.create_knowledge_page("p","overview","Overview","Unique alpha project's detail.")
    store.create_knowledge_page("q","overview","Overview","Unique beta project's detail.")
    assert store.search("p","alpha")["results"][0]["path"]==p["path"]
    assert store.search("q","alpha")["results"]==[]
    assert "beta" not in store.read_knowledge_page("p","overview")["content"]
    first=store.list_knowledge_pages("p",limit=1)
    assert first["count"]==6 and len(first["pages"])==1 and first["next_offset"]==1
    long=store.create_knowledge_page("p","long","Long page","x "*9000)
    read=store.read_knowledge_page("p","long")
    assert read["content_truncated"] is True and len(read["content"])==12000
    assert len(long["sha256"])==64


def test_page_id_and_symlink_guards_prevent_escape(setup,tmp_path):
    store,_=setup
    for bad in ("../outside","folder/page","requirements","D:","-leading"):
        with pytest.raises(ValueError): store.create_knowledge_page("p",bad,"Bad","must not write")
    target=tmp_path/"outside.md"; target.write_text("PAGE_ESCAPE_CANARY_9912",encoding="utf-8")
    link=Path(store.get_project("p")["memory_root"])/"knowledge"/"outside.md"
    try: link.symlink_to(target)
    except OSError: pytest.skip("Windows symlink creation is not enabled for this account")
    with pytest.raises(ValueError,match="symlink"):
        store.read_knowledge_page("p","outside")
    store.index_project("p")
    assert store.search("p","PAGE_ESCAPE_CANARY_9912")["results"]==[]

