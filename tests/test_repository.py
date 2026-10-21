from __future__ import annotations

import shutil
import subprocess
import time
import os
import sys
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agentmem.repository import RepositoryIndexService


def make_service(tmp_path: Path, *, max_files: int = 100):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    data = tmp_path / "state"
    projects = []
    for project_id in ("p", "other"):
        memory_root = tmp_path / "kb" / project_id
        memory_root.mkdir(parents=True, exist_ok=True)
        marker = memory_root / ".agentmem-project.json"
        if not marker.exists():
            marker.write_text(json.dumps({"project_id": project_id, "source_root": str(root), "format": 1}), encoding="utf-8")
        projects.append({"id": project_id, "root": str(root), "memory_root": str(memory_root)})

    def project_lookup(project_id: str):
        project = next((item for item in projects if item["id"] == project_id), None)
        if project is None:
            raise ValueError("Unknown project")
        return project

    return RepositoryIndexService(data, project_lookup, project_list=lambda: projects, poll_interval=0.1,
                                   debounce_seconds=0.15, max_files=max_files), root


def test_repository_search_cites_incremental_changes_and_removes_deleted_or_ignored_files(tmp_path: Path):
    service, root = make_service(tmp_path)
    (root / "README.md").write_text("The initial retry policy uses four attempts.\n", encoding="utf-8")
    (root / "old.md").write_text("The old launch codename is bluejay.\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored.md\n", encoding="utf-8")
    (root / "ignored.md").write_text("The ignored marker is cedar.\n", encoding="utf-8")
    (root / ".env").write_text("API_KEY=0123456789abcdefghijklmnop\n", encoding="utf-8")
    (root / "secrets").mkdir()
    (root / "secrets" / "private.md").write_text("privatefoldermarker value\n", encoding="utf-8")
    try:
        registered = service.register("p", root)
        repo_id = registered["repository_id"]
        assert registered["initial_sync"]["scan_complete"]
        assert service.search("p", repo_id, "bluejay")["results"]
        assert service.search("p", repo_id, "cedar")["results"] == []
        assert service.search("p", repo_id, "API_KEY")["results"] == []
        assert service.search("p", repo_id, "privatefoldermarker")["results"] == []
        # This test exercises explicit resync semantics; stop the automatically
        # started watcher so it cannot commit deletions before assertions below.
        service.stop()

        readme = root / "README.md"
        readme.write_text("The updated retry policy uses seven attempts.\n", encoding="utf-8")
        result = service.resync("p", repo_id)
        assert result["files_changed"] == 1
        updated = service.search("p", repo_id, "seven")
        assert updated["results"][0]["citation"].startswith("README.md#L1-L1")
        assert updated["results"][0]["fresh"] is True
        assert service.search("p", repo_id, "four")["results"] == []

        old_stat = readme.stat()
        readme.write_text("The updated retry policy uses six attempts.\n", encoding="utf-8")
        os.utime(readme, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        preserved_metadata = service.search("p", repo_id, "seven")
        assert preserved_metadata["results"][0]["freshness_level"] == "content_changed"
        forced = service.resync("p", repo_id)
        assert forced["files_changed"] == 1
        assert service.search("p", repo_id, "six")["results"][0]["fresh"] is True

        newly_ignored = root / "later-ignored.md"
        newly_ignored.write_text("laterignoredmarker value\n", encoding="utf-8")
        service.resync("p", repo_id)
        assert service.search("p", repo_id, "laterignoredmarker")["results"]
        with (root / ".gitignore").open("a", encoding="utf-8") as handle:
            handle.write("later-ignored.md\n")
        service.resync("p", repo_id)
        assert service.search("p", repo_id, "laterignoredmarker")["results"] == []

        (root / "old.md").unlink()
        (root / "ignored.md").unlink()
        deleted = service.resync("p", repo_id)
        assert deleted["files_deleted"] >= 1
        assert service.search("p", repo_id, "bluejay")["results"] == []
    finally:
        service.stop()


def test_repository_partial_scan_does_not_delete_unseen_indexed_rows(tmp_path: Path):
    service, root = make_service(tmp_path, max_files=100)
    (root / "a.md").write_text("partialscanfirst token.\n", encoding="utf-8")
    (root / "b.md").write_text("partialscansecond token.\n", encoding="utf-8")
    try:
        result = service.register("p", root)
        repo_id = result["repository_id"]
        assert result["initial_sync"]["scan_complete"]
        first = service.search("p", repo_id, "partialscanfirst")
        assert first["results"]
        indexed_path = Path(root / first["results"][0]["path"])
        indexed_path.unlink()
        (root / "c.md").write_text("partialscanc marker.\n", encoding="utf-8")
        (root / "d.md").write_text("partialscand marker.\n", encoding="utf-8")
        partial = service.resync("p", repo_id, max_files=1)
        assert partial["scan_truncated"]
        # The old row remains until a complete traversal can prove deletion.
        stale = service.search("p", repo_id, "partialscanfirst")
        assert stale["results"]
        assert stale["results"][0]["freshness_level"] == "source_unavailable"
    finally:
        service.stop()


def test_repository_ids_and_indexes_are_project_scoped_and_worktree_scoped(tmp_path: Path):
    service, root = make_service(tmp_path)
    (root / "code.py").write_text("projectisolationmarker = 1\n", encoding="utf-8")
    try:
        p_repo = service.register("p", root)["repository_id"]
        p_again = service.register("p", root)["repository_id"]
        other_repo = service.register("other", root)["repository_id"]
        assert p_repo == p_again
        assert p_repo != other_repo
        assert service.search("p", p_repo, "projectisolationmarker")["results"]
        assert service.search("other", other_repo, "projectisolationmarker")["results"]
        with pytest.raises(ValueError, match="not found"):
            service.search("p", other_repo, "projectisolationmarker")
    finally:
        service.stop()


def test_repository_databases_and_locks_live_under_each_project_knowledge_root(tmp_path: Path):
    service, root = make_service(tmp_path)
    (root / "code.py").write_text("layoutmigrationmarker = True\n", encoding="utf-8")
    try:
        p_repo = service.register("p", root)["repository_id"]
        other_repo = service.register("other", root)["repository_id"]
        p_db, p_locks = service._project_storage("p")
        other_db, other_locks = service._project_storage("other")
        assert p_db == tmp_path / "kb" / "p" / "repository-index" / "repositories.sqlite3"
        assert other_db == tmp_path / "kb" / "other" / "repository-index" / "repositories.sqlite3"
        assert p_db != other_db and p_locks != other_locks
        assert not (tmp_path / "state" / "repository-index" / "repositories.sqlite3").exists()
        assert service.search("p", p_repo, "layoutmigrationmarker")["results"]
        assert service.search("other", other_repo, "layoutmigrationmarker")["results"]
    finally:
        service.stop()


def test_legacy_shared_repository_database_migrates_to_project_store_and_is_backed_up(tmp_path: Path):
    service, root = make_service(tmp_path)
    source = root / "legacy.md"
    source.write_text("legacyindexmigrationmarker retained\n", encoding="utf-8")
    other_source = root / "other.md"
    other_source.write_text("otherprojectindexmarker isolated\n", encoding="utf-8")
    legacy_dir = tmp_path / "state" / "repository-index"
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "repositories.sqlite3"
    repo_id = "repo-existing-id"
    now = time.time()
    with sqlite3.connect(legacy_db) as db:
        RepositoryIndexService._init_db(db)
        db.execute("INSERT INTO repositories(project_id,repository_id,root,index_revision,indexed_at,last_scan_complete,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                   ("p", repo_id, str(root.resolve()), 7, now, 1, now - 10, now))
        db.execute("INSERT INTO repository_files(project_id,repository_id,relpath,sha256,size_bytes,mtime_ns,indexed_at) VALUES(?,?,?,?,?,?,?)",
                   ("p", repo_id, "legacy.md", "abc", source.stat().st_size, source.stat().st_mtime_ns, now))
        db.execute("INSERT INTO repository_chunks(project_id,repository_id,relpath,sha256,line_start,line_end,body) VALUES(?,?,?,?,?,?,?)",
                   ("p", repo_id, "legacy.md", "abc", 1, 1, "legacyindexmigrationmarker retained"))
        other_id = "repo-existing-other"
        db.execute("INSERT INTO repositories(project_id,repository_id,root,index_revision,indexed_at,last_scan_complete,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                   ("other", other_id, str(root.resolve()), 3, now, 1, now - 10, now))
        db.execute("INSERT INTO repository_files(project_id,repository_id,relpath,sha256,size_bytes,mtime_ns,indexed_at) VALUES(?,?,?,?,?,?,?)",
                   ("other", other_id, "other.md", "def", other_source.stat().st_size, other_source.stat().st_mtime_ns, now))
        db.execute("INSERT INTO repository_chunks(project_id,repository_id,relpath,sha256,line_start,line_end,body) VALUES(?,?,?,?,?,?,?)",
                   ("other", other_id, "other.md", "def", 1, 1, "otherprojectindexmarker isolated"))
    db.close()
    project_lookup = service.project_lookup
    service = RepositoryIndexService(tmp_path / "state", project_lookup,
                                     project_list=lambda: [project_lookup("p"), project_lookup("other")],
                                     poll_interval=0.1)
    try:
        assert not legacy_dir.exists()
        backups = list((tmp_path / "state" / "migration-backups").glob("repository-index-legacy-*"))
        assert len(backups) == 1
        assert (backups[0] / "repositories.sqlite3").is_file()
        assert service._project_storage("p")[0].is_file()
        assert service.status("p", repo_id)["index_revision"] == 7
        result = service.search("p", repo_id, "legacyindexmigrationmarker")["results"]
        assert result[0]["citation"] == "legacy.md#L1-L1"
        assert service.status("other", other_id)["index_revision"] == 3
        assert service.search("other", other_id, "otherprojectindexmarker")["results"]
        assert service._project_storage("p")[0] != service._project_storage("other")[0]
    finally:
        service.stop()


def test_legacy_migration_keeps_source_when_project_identity_is_unavailable(tmp_path: Path):
    service, root = make_service(tmp_path)
    legacy_dir = tmp_path / "state" / "repository-index"
    legacy_dir.mkdir(parents=True)
    with sqlite3.connect(legacy_dir / "repositories.sqlite3") as db:
        RepositoryIndexService._init_db(db)
        now = time.time()
        db.execute("INSERT INTO repositories(project_id,repository_id,root,created_at,updated_at) VALUES(?,?,?,?,?)",
                   ("missing-project", "repo-legacy", str(root.resolve()), now, now))
    db.close()
    with pytest.raises(RuntimeError, match="unregistered projects"):
        RepositoryIndexService(tmp_path / "state", service.project_lookup, project_list=lambda: [], poll_interval=0.1)
    assert (legacy_dir / "repositories.sqlite3").is_file()
    assert not (tmp_path / "state" / "migration-backups").exists()


def test_repository_index_skips_symlinks_that_escape_the_registered_root(tmp_path: Path):
    service, root = make_service(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("externalescapemarker private\n", encoding="utf-8")
    try:
        (root / "escape.md").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    try:
        repo_id = service.register("p", root)["repository_id"]
        assert service.search("p", repo_id, "externalescapemarker")["results"] == []
    finally:
        service.stop()


def test_repository_watcher_resumes_and_serializes_shared_clients(tmp_path: Path):
    first, root = make_service(tmp_path)
    (root / "README.md").write_text("watcherinitialmarker\n", encoding="utf-8")
    second, _ = make_service(tmp_path)
    try:
        registered = first.register("p", root)
        repo_id = registered["repository_id"]
        second.start()
        first_status = first.status("p", repo_id)
        second_status = second.status("p", repo_id)
        assert first_status["watcher_pid"] == second_status["watcher_pid"]

        old_revision = first_status["index_revision"]
        (root / "README.md").write_text("watcherupdatedmarker\n", encoding="utf-8")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if first.status("p", repo_id)["index_revision"] > old_revision:
                break
            time.sleep(0.05)
        assert first.status("p", repo_id)["index_revision"] > old_revision
        assert second.search("p", repo_id, "watcherupdatedmarker")["results"]
        assert first.search("p", repo_id, "watcherinitialmarker")["results"] == []

        # Native filesystem events must catch content edits even when metadata is restored.
        file_path = root / "README.md"
        prior_stat = file_path.stat()
        same_size_marker = "watchereventsamebytes\n"
        assert len(same_size_marker.encode()) == prior_stat.st_size
        event_revision = first.status("p", repo_id)["index_revision"]
        file_path.write_text(same_size_marker, encoding="utf-8")
        os.utime(file_path, ns=(prior_stat.st_atime_ns, prior_stat.st_mtime_ns))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and first.status("p", repo_id)["index_revision"] <= event_revision:
            time.sleep(0.05)
        assert first.status("p", repo_id)["index_revision"] > event_revision
        assert second.search("p", repo_id, "watchereventsamebytes")["results"]

        first.stop()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = second.status("p", repo_id)
            if status["watcher_state"] == "current":
                break
            time.sleep(0.05)
        assert second.status("p", repo_id)["watcher_state"] == "current"
        failover_revision = second.status("p", repo_id)["index_revision"]
        (root / "README.md").write_text("watcherfailovermarker\n", encoding="utf-8")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and second.status("p", repo_id)["index_revision"] <= failover_revision:
            time.sleep(0.05)
        assert second.status("p", repo_id)["index_revision"] > failover_revision
        assert second.search("p", repo_id, "watcherfailovermarker")["results"]
    finally:
        second.stop()
        first.stop()


def test_repository_watcher_lease_is_shared_across_processes(tmp_path: Path):
    service, root = make_service(tmp_path)
    try:
        repo_id = service.register("p", root)["repository_id"]
        service.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and service.status("p", repo_id)["watcher_state"] != "current":
            time.sleep(0.05)
        child_code = r'''
import json, sys, time
from agentmem.repository import RepositoryIndexService
data, root, repo_id, memory_root = sys.argv[1:]
project = {"id": "p", "root": root, "memory_root": memory_root}
service = RepositoryIndexService(data, lambda project_id: project,
                                 project_list=lambda: [project], poll_interval=0.1)
service.start()
time.sleep(0.4)
print(json.dumps(service.status("p", repo_id)))
service.stop()
'''
        env = os.environ.copy()
        source_dir = str(Path(__file__).resolve().parents[1] / "src")
        env["PYTHONPATH"] = source_dir + os.pathsep + env.get("PYTHONPATH", "")
        child = subprocess.run([sys.executable, "-c", child_code, str(tmp_path / "state"), str(root), repo_id,
                                str(tmp_path / "kb" / "p")],
                               env=env, capture_output=True, text=True, timeout=10, check=True)
        child_status = json.loads(child.stdout.strip().splitlines()[-1])
        assert child_status["watcher_pid"] == os.getpid()
    finally:
        service.stop()


def test_repository_watcher_startup_reconciliation_finds_metadata_preserving_edits(tmp_path: Path):
    service, root = make_service(tmp_path)
    file_path = root / "README.md"
    file_path.write_text("startupindexoldmarker\n", encoding="utf-8")
    try:
        repo_id = service.register("p", root)["repository_id"]
        old_stat = file_path.stat()
        revision = service.status("p", repo_id)["index_revision"]
        file_path.write_text("startupindexnewmarker\n", encoding="utf-8")
        os.utime(file_path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        service.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and service.status("p", repo_id)["index_revision"] <= revision:
            time.sleep(0.05)
        assert service.status("p", repo_id)["index_revision"] > revision
        assert service.search("p", repo_id, "startupindexnewmarker")["results"]
        assert service.search("p", repo_id, "startupindexoldmarker")["results"] == []
    finally:
        service.stop()


def test_repository_serializes_concurrent_resync_while_search_reads_committed_snapshot(tmp_path: Path):
    first, root = make_service(tmp_path)
    second, _ = make_service(tmp_path)
    (root / "large.txt").write_text(("concurrentindexmarker stable line\n" * 2_000), encoding="utf-8")
    try:
        repo_id = first.register("p", root)["repository_id"]
        second.start()
        with ThreadPoolExecutor(max_workers=3) as pool:
            writes = [pool.submit(service.resync, "p", repo_id) for service in (first, second)]
            reads = [pool.submit(service.search, "p", repo_id, "concurrentindexmarker") for service in (first, second)]
            outcomes = [future.result(timeout=10) for future in writes + reads]
        assert outcomes[0]["scan_complete"] and outcomes[1]["scan_complete"]
        assert all(outcome["results"] for outcome in outcomes[2:])
        assert all(outcome["results"][0]["fresh"] for outcome in outcomes[2:])
    finally:
        second.stop()
        first.stop()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is needed for worktree metadata validation")
def test_repository_worktrees_have_distinct_index_identity_and_shared_git_common_dir(tmp_path: Path):
    service, root = make_service(tmp_path)
    root.mkdir(exist_ok=True)
    (root / "code.py").write_text("worktreecode", encoding="utf-8")
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "code.py"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "init"], check=True, capture_output=True)
    worktree = tmp_path / "worktree-two"
    subprocess.run(["git", "-C", str(root), "worktree", "add", "-b", "second", str(worktree)], check=True, capture_output=True)
    try:
        one = service.register("p", root)
        two = service.register("p", worktree)
        assert one["repository_id"] != two["repository_id"]
        assert one["git_common_dir"] == two["git_common_dir"]
        assert two["branch"] == "second"
    finally:
        service.stop()
