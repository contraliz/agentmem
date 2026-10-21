from __future__ import annotations

import hashlib
import json
import fnmatch
import os
import re
import sqlite3
import subprocess
import threading
import time
import tempfile
from pathlib import Path
from typing import Any, Callable
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .store import (
    EXCLUDED_DIRS,
    MAX_FILE_BYTES,
    SECRET_ASSIGNMENT,
    SECRET_NAME,
    SECRET_SUFFIXES,
    SENSITIVE_BASENAMES,
    TEXT_SUFFIXES,
)

try:  # The watcher lease is advisory and released by the OS on process exit.
    import fcntl
except ImportError:  # pragma: no cover - Windows path
    fcntl = None


_TOKEN = re.compile(r"[\w]+", re.UNICODE)
_MAX_QUERY_TOKENS = 24
_MAX_RESULTS = 20
_MAX_SCAN_FILES = 20_000
_CHUNK_LINES = 35
_CHUNK_CHARS = 5_000
_REPARSE_POINT = 0x400
_PROCESS_LOCK_GUARD = threading.Lock()
_PROCESS_LOCK_PATHS: set[str] = set()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _reparse(path: Path) -> bool:
    try:
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            return True
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & _REPARSE_POINT)
    except OSError:
        return True


class _ProcessLock:
    """Small cross-process advisory lock, held for the watcher ownership lease."""

    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        identity = os.path.normcase(str(self.path.resolve()))
        with _PROCESS_LOCK_GUARD:
            if identity in _PROCESS_LOCK_PATHS:
                return False
        self.file = self.path.open("a+b")
        self.file.seek(0, os.SEEK_END)
        if self.file.tell() == 0:
            self.file.write(b"\0")
            self.file.flush()
        self.file.seek(0)
        try:
            if fcntl is not None:
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:  # pragma: no cover - exercised on Windows
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            with _PROCESS_LOCK_GUARD:
                _PROCESS_LOCK_PATHS.add(identity)
            self._identity = identity
            return True
        except OSError:
            self.file.close()
            self.file = None
            return False

    def release(self) -> None:
        if self.file is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            else:  # pragma: no cover - exercised on Windows
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.file.close()
            self.file = None
            identity = getattr(self, "_identity", None)
            if identity:
                with _PROCESS_LOCK_GUARD:
                    _PROCESS_LOCK_PATHS.discard(identity)
                self._identity = None


class RepositoryIndexService:
    """Independent, rebuildable repository corpus with a process-shared watcher lease."""

    def __init__(
        self,
        data_dir: Path | str,
        project_lookup: Callable[[str], dict[str, Any]],
        *,
        project_list: Callable[[], list[dict[str, Any]]] | None = None,
        poll_interval: float = 1.0,
        debounce_seconds: float = 0.6,
        reconcile_interval: float = 60.0,
        max_files: int = _MAX_SCAN_FILES,
    ):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if _reparse(self.data_dir):
            raise ValueError("Repository index data directory cannot be a symlink or reparse point")
        self.project_lookup = project_lookup
        self.project_list = project_list or (lambda: [])
        self._known_projects: set[str] = set()
        self.poll_interval = max(0.2, float(poll_interval))
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self.reconcile_interval = max(1.0, float(reconcile_interval))
        self.max_files = max(1, min(int(max_files), _MAX_SCAN_FILES))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._owners: dict[tuple[str, str], _ProcessLock] = {}
        self._observers: dict[tuple[str, str], Observer] = {}
        self._pending_since: dict[tuple[str, str], float] = {}
        self._pending_paths: dict[tuple[str, str], set[str]] = {}
        self._pending_full: set[tuple[str, str]] = set()
        self._last_reconcile: dict[tuple[str, str], float] = {}
        self._mutex = threading.RLock()
        self._migrate_legacy_index()

    def _project_storage(self, project_id: str, *, create: bool = True) -> tuple[Path, Path]:
        project = self.project_lookup(project_id)
        if project.get("id") != project_id:
            raise ValueError("Project resolver returned a mismatched project identity")
        memory_root = Path(project["memory_root"]).expanduser().absolute()
        if _reparse(memory_root) or not memory_root.is_dir() or memory_root.resolve(strict=True) != memory_root:
            raise ValueError("Registered project knowledge directory is missing or unsafe")
        manifest = memory_root / ".agentmem-project.json"
        if _reparse(manifest) or not manifest.is_file():
            raise ValueError("Registered project knowledge manifest is missing or unsafe")
        try:
            metadata = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Registered project knowledge manifest is invalid") from exc
        if metadata.get("project_id") != project_id:
            raise ValueError("Project knowledge manifest does not match the registered project")
        if Path(metadata.get("source_root", "")).expanduser().resolve() != Path(project["root"]).expanduser().resolve():
            raise ValueError("Project knowledge manifest source root does not match the registry")
        self._known_projects.add(project_id)
        state_dir = memory_root / "repository-index"
        if create:
            state_dir.mkdir(exist_ok=True)
        if state_dir.exists() and (_reparse(state_dir) or state_dir.resolve(strict=True).parent != memory_root):
            raise ValueError("Repository index state directory is unsafe")
        lock_dir = state_dir / "locks"
        if create:
            lock_dir.mkdir(exist_ok=True)
        if lock_dir.exists() and (_reparse(lock_dir) or lock_dir.resolve(strict=True).parent != state_dir):
            raise ValueError("Repository index lock directory is unsafe")
        return state_dir / "repositories.sqlite3", lock_dir

    def _connect(self, project_id: str) -> sqlite3.Connection:
        db_path, _ = self._project_storage(project_id)
        if db_path.exists() and _reparse(db_path):
            raise ValueError("Repository index database cannot be a symlink or reparse point")
        initialize = not db_path.exists()
        db = sqlite3.connect(db_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA foreign_keys=ON")
        if initialize:
            self._init_db(db)
        return db

    @staticmethod
    def _init_db(db: sqlite3.Connection) -> None:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
                CREATE TABLE IF NOT EXISTS repositories(
                    project_id TEXT NOT NULL,
                    repository_id TEXT NOT NULL,
                    root TEXT NOT NULL,
                    git_common_dir TEXT NOT NULL DEFAULT '',
                    branch TEXT NOT NULL DEFAULT '',
                    head_sha TEXT NOT NULL DEFAULT '',
                    dirty INTEGER NOT NULL DEFAULT 0,
                    index_revision INTEGER NOT NULL DEFAULT 0,
                    indexed_at REAL NOT NULL DEFAULT 0,
                    watcher_pid INTEGER NOT NULL DEFAULT 0,
                    watcher_heartbeat REAL NOT NULL DEFAULT 0,
                    pending_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    last_scan_complete INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(project_id, repository_id),
                    UNIQUE(project_id, root)
                );
                CREATE TABLE IF NOT EXISTS repository_files(
                    project_id TEXT NOT NULL,
                    repository_id TEXT NOT NULL,
                    relpath TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    indexed_at REAL NOT NULL,
                    PRIMARY KEY(project_id, repository_id, relpath),
                    FOREIGN KEY(project_id,repository_id) REFERENCES repositories(project_id,repository_id) ON DELETE CASCADE
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS repository_chunks USING fts5(
                    project_id UNINDEXED, repository_id UNINDEXED, relpath UNINDEXED,
                    sha256 UNINDEXED, line_start UNINDEXED, line_end UNINDEXED, body,
                    tokenize='unicode61 remove_diacritics 2'
                );
        """)

    def _ensure_project_db(self, project_id: str) -> None:
        with self._connect(project_id) as db:
            self._init_db(db)

    def _migrate_legacy_index(self) -> None:
        """Move the former shared index into per-project stores without discarding its source."""
        legacy_dir = self.data_dir / "repository-index"
        legacy_db = legacy_dir / "repositories.sqlite3"
        if not legacy_db.exists():
            return
        if _reparse(legacy_dir) or _reparse(legacy_db):
            raise ValueError("Legacy repository index is unsafe; preserve it and resolve before restart")
        projects = {item["id"]: item for item in self.project_list()}
        # Use an OS lock outside the directory so the source can be renamed after verification.
        migration_lock = _ProcessLock(self.data_dir / "repository-index-migration.lock")
        deadline = time.monotonic() + 30.0
        while not migration_lock.acquire():
            if not legacy_db.exists():
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("Legacy repository index migration is active in another AgentMem process")
            time.sleep(0.1)
        try:
            if not legacy_db.exists():
                return
            with tempfile.TemporaryDirectory(prefix="agentmem-repo-migrate-") as scratch:
                snapshot = Path(scratch) / "legacy.sqlite3"
                source = sqlite3.connect(legacy_db, timeout=15)
                try:
                    source.execute("PRAGMA busy_timeout=15000")
                    target = sqlite3.connect(snapshot)
                    try:
                        source.backup(target)
                    finally:
                        target.close()
                finally:
                    source.close()
                legacy = sqlite3.connect(snapshot)
                legacy.row_factory = sqlite3.Row
                try:
                    integrity = legacy.execute("PRAGMA integrity_check").fetchone()[0]
                    if integrity != "ok":
                        raise RuntimeError(f"Legacy repository index failed integrity check: {integrity}")
                    rows = [dict(row) for row in legacy.execute("SELECT * FROM repositories ORDER BY project_id,repository_id")]
                    for project_id in {row["project_id"] for row in rows} - projects.keys():
                        try:
                            project = self.project_lookup(project_id)
                        except ValueError:
                            continue
                        if project.get("id") != project_id:
                            raise ValueError("Project resolver returned a mismatched project identity")
                        projects[project_id] = project
                    missing = sorted({row["project_id"] for row in rows} - projects.keys())
                    if missing:
                        raise RuntimeError("Legacy repository index references unregistered projects: " + ", ".join(missing))
                    for repository in rows:
                        project_id, repo_id = repository["project_id"], repository["repository_id"]
                        self._ensure_project_db(project_id)
                        with self._connect(project_id) as dest:
                            dest.execute("BEGIN IMMEDIATE")
                            current = dest.execute("SELECT * FROM repositories WHERE project_id=? AND repository_id=?",
                                                   (project_id, repo_id)).fetchone()
                            use_legacy = current is None or float(repository["updated_at"]) >= float(current["updated_at"])
                            if use_legacy:
                                dest.execute("DELETE FROM repository_chunks WHERE project_id=? AND repository_id=?", (project_id, repo_id))
                                dest.execute("DELETE FROM repository_files WHERE project_id=? AND repository_id=?", (project_id, repo_id))
                                columns = list(repository)
                                dest.execute(f"INSERT OR REPLACE INTO repositories({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                                             [repository[column] for column in columns])
                                for table in ("repository_files", "repository_chunks"):
                                    source_rows = legacy.execute(f"SELECT * FROM {table} WHERE project_id=? AND repository_id=?",
                                                                 (project_id, repo_id)).fetchall()
                                    for source_row in source_rows:
                                        data = dict(source_row)
                                        cols = list(data)
                                        dest.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",
                                                     [data[column] for column in cols])
                            dest.commit()
                            repo_count = dest.execute("SELECT COUNT(*) FROM repositories WHERE project_id=? AND repository_id=?",
                                                      (project_id, repo_id)).fetchone()[0]
                            if repo_count != 1:
                                raise RuntimeError(f"Could not verify migrated repository {project_id}/{repo_id}")
                            if use_legacy:
                                for table in ("repository_files", "repository_chunks"):
                                    expected = legacy.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id=? AND repository_id=?",
                                                              (project_id, repo_id)).fetchone()[0]
                                    actual = dest.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id=? AND repository_id=?",
                                                          (project_id, repo_id)).fetchone()[0]
                                    if actual != expected:
                                        raise RuntimeError(f"Could not verify migrated {table} for {project_id}/{repo_id}")
                            integrity = dest.execute("PRAGMA integrity_check").fetchone()[0]
                            if integrity != "ok":
                                raise RuntimeError(f"Migrated project index failed integrity check: {integrity}")
                    # Every source project/worktree is present and transactionally copied before preserving the source.
                    backup_root = self.data_dir / "migration-backups"
                    backup_root.mkdir(exist_ok=True)
                    backup = backup_root / f"repository-index-legacy-{time.time_ns()}"
                    legacy.close()
                    legacy = None
                    os.replace(legacy_dir, backup)
                finally:
                    if legacy is not None:
                        legacy.close()
        finally:
            migration_lock.release()

    def start(self) -> None:
        """Start this MCP process's coordinator; a file lease elects one writer per worktree."""
        with self._mutex:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._watch_loop, name="agentmem-repository-watcher", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._mutex:
            self._stop.set()
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
        if thread and thread.is_alive():
            return
        with self._mutex:
            observers = list(self._observers.values())
            self._observers.clear()
            locks = list(self._owners.values())
            self._owners.clear()
        for observer in observers:
            observer.stop()
        for observer in observers:
            observer.join(timeout=max(0.0, timeout))
        with self._mutex:
            self._pending_paths.clear()
            self._pending_full.clear()
            self._last_reconcile.clear()
            self._thread = None
        for lock in locks:
            lock.release()

    def _start_observer(self, row: dict[str, Any]) -> None:
        repo_id = row["repository_id"]
        repo_key = (row["project_id"], repo_id)
        service = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event: Any) -> None:
                if event.event_type in {"opened", "closed_no_write"}:
                    return
                try:
                    rel = Path(event.src_path).relative_to(Path(row["root"])).as_posix()
                except ValueError:
                    return
                if rel == ".git" or rel.startswith(".git/"):
                    return
                with service._mutex:
                    if getattr(event, "is_directory", False):
                        service._pending_full.add(repo_key)
                    else:
                        service._pending_paths.setdefault(repo_key, set()).add(rel)
                    service._pending_since.setdefault(repo_key, time.monotonic())

        observer = Observer()
        observer.schedule(Handler(), str(row["root"]), recursive=True)
        observer.start()
        self._observers[repo_key] = observer

    def _stop_observer(self, repo_key: tuple[str, str]) -> None:
        observer = self._observers.pop(repo_key, None)
        if observer is not None:
            observer.stop()
            observer.join(timeout=2.0)

    def _repo_rows(self) -> list[dict[str, Any]]:
        rows = []
        projects = {item["id"]: item for item in self.project_list()}
        for project_id in self._known_projects:
            if project_id not in projects:
                projects[project_id] = self.project_lookup(project_id)
        for project in projects.values():
            project_id = project["id"]
            if not self._project_storage(project_id)[0].exists():
                continue
            with self._connect(project_id) as db:
                rows.extend(dict(row) for row in db.execute("SELECT * FROM repositories ORDER BY repository_id"))
        return rows

    def _repo_row(self, project_id: str, repository_id: str) -> dict[str, Any]:
        with self._connect(project_id) as db:
            row = db.execute("SELECT * FROM repositories WHERE project_id=? AND repository_id=?", (project_id, repository_id)).fetchone()
        if not row:
            raise ValueError("Repository not found for this project; call repository_list to inspect registered roots")
        return dict(row)

    @staticmethod
    def _canonical_root(root: str | Path) -> Path:
        supplied = Path(root).expanduser()
        if not supplied.is_absolute():
            raise ValueError("repository_root must be an absolute path")
        if _reparse(supplied):
            raise ValueError("Repository root cannot be a symlink, junction, or reparse point")
        resolved = supplied.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("repository_root must name an existing directory")
        return resolved

    @staticmethod
    def _git_metadata(root: Path) -> dict[str, Any]:
        def run(*args: str) -> str:
            try:
                result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                                        timeout=2.0, check=False)
                return result.stdout.strip() if result.returncode == 0 else ""
            except (OSError, subprocess.SubprocessError):
                return ""

        common = run("rev-parse", "--git-common-dir")
        if common:
            common_path = Path(common)
            common = str((common_path if common_path.is_absolute() else root / common_path).resolve())
        return {
            "git_common_dir": common,
            "branch": run("branch", "--show-current"),
            "head_sha": run("rev-parse", "HEAD"),
            "dirty": bool(run("status", "--porcelain")),
        }

    def register(self, project_id: str, repository_root: str | Path) -> dict[str, Any]:
        project = self.project_lookup(project_id)
        if project.get("id") != project_id:
            raise ValueError("Project resolver returned a mismatched project identity")
        self._ensure_project_db(project_id)
        root = self._canonical_root(repository_root)
        normalized = os.path.normcase(str(root))
        repo_id = "repo-" + hashlib.sha256((project_id + "\0" + normalized).encode("utf-8")).hexdigest()[:20]
        git = self._git_metadata(root)
        now = time.time()
        with self._connect(project_id) as db:
            db.execute("BEGIN IMMEDIATE")
            conflict = db.execute("SELECT repository_id FROM repositories WHERE project_id=? AND root=?", (project_id, str(root))).fetchone()
            if conflict:
                repo_id = conflict["repository_id"]
                db.execute("UPDATE repositories SET git_common_dir=?,branch=?,head_sha=?,dirty=?,updated_at=? WHERE project_id=? AND repository_id=?",
                           (git["git_common_dir"], git["branch"], git["head_sha"], int(git["dirty"]), now, project_id, repo_id))
            else:
                db.execute("INSERT INTO repositories(project_id,repository_id,root,git_common_dir,branch,head_sha,dirty,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (project_id, repo_id, str(root), git["git_common_dir"], git["branch"], git["head_sha"], int(git["dirty"]), now, now))
            db.commit()
        self.start()
        sync = self.resync(project_id, repo_id)
        return {"project_id": project_id, "repository_id": repo_id, "root": str(root),
                "git_common_dir": git["git_common_dir"], "branch": git["branch"], "head_sha": git["head_sha"],
                "dirty": git["dirty"], "initial_sync": sync, "status": self.status(project_id, repo_id)}

    def list(self, project_id: str) -> dict[str, Any]:
        self.project_lookup(project_id)
        with self._connect(project_id) as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM repositories WHERE project_id=? ORDER BY root", (project_id,))]
        return {"project_id": project_id, "repositories": [self._public_status(row) for row in rows]}

    def _public_status(self, row: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        age = now - float(row.get("watcher_heartbeat", 0) or 0)
        watcher = "current" if age <= max(self.poll_interval * 4, 5) else "stale" if row.get("watcher_pid") else "not_running"
        freshness = ("current" if watcher == "current" and not row.get("pending_count")
                     and row.get("last_scan_complete") and not row.get("last_error") else "stale")
        return {
            "project_id": row["project_id"], "repository_id": row["repository_id"], "root": row["root"],
            "git_common_dir": row["git_common_dir"], "branch": row["branch"], "head_sha": row["head_sha"],
            "dirty": bool(row["dirty"]), "index_revision": int(row["index_revision"]),
            "indexed_at": row["indexed_at"] or None, "watcher_pid": int(row["watcher_pid"] or 0) or None,
            "watcher_heartbeat": row["watcher_heartbeat"] or None, "watcher_state": watcher,
            "pending_changes": int(row["pending_count"]), "freshness": freshness,
            "last_scan_complete": bool(row["last_scan_complete"]), "last_error": row["last_error"] or "",
        }

    def status(self, project_id: str, repository_id: str) -> dict[str, Any]:
        self.project_lookup(project_id)
        return self._public_status(self._repo_row(project_id, repository_id))

    @staticmethod
    def _iter_regular_files(root: Path, limit: int) -> tuple[list[tuple[str, Path, os.stat_result]], bool]:
        found: list[tuple[str, Path, os.stat_result]] = []
        truncated = False
        stack = [root]
        while stack:
            parent = stack.pop()
            try:
                with os.scandir(parent) as scanned:
                    entries = sorted(scanned, key=lambda entry: entry.name.casefold())
            except OSError:
                truncated = True
                continue
            directories: list[Path] = []
            for entry in entries:
                path = Path(entry.path)
                if _reparse(path):
                    continue
                try:
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    truncated = True
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if (entry.name.casefold() not in EXCLUDED_DIRS
                            and not entry.name.casefold().endswith(".egg-info")
                            and not SECRET_NAME.search(entry.name)
                            and entry.name.casefold() not in SENSITIVE_BASENAMES):
                        directories.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                rel = path.relative_to(root).as_posix()
                found.append((rel, path, stat))
                if len(found) > limit:
                    truncated = True
                    break
            if truncated:
                break
            stack.extend(reversed(directories))
        return found[:limit], truncated

    @staticmethod
    def _ignored_fallback(root: Path, relpaths: list[str]) -> set[str]:
        rules: list[tuple[str, str, bool]] = []
        ignore_files = [path for _, path, _ in RepositoryIndexService._iter_regular_files(root, _MAX_SCAN_FILES)[0]
                        if path.name == ".gitignore"]
        for ignore_file in ignore_files:
            if _reparse(ignore_file):
                continue
            try:
                base = ignore_file.parent.relative_to(root).as_posix()
                if base == ".":
                    base = ""
                for raw in ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    negate = line.startswith("!")
                    pattern = line[1:] if negate else line
                    rules.append((base, pattern, negate))
            except OSError:
                continue
        ignored: set[str] = set()
        for rel in relpaths:
            excluded = False
            for base, pattern, negate in rules:
                local = rel[len(base) + 1:] if base and rel.startswith(base + "/") else rel if not base else ""
                if not local:
                    continue
                if fnmatch.fnmatchcase(local, pattern) or (pattern.endswith("/") and local.startswith(pattern)):
                    excluded = not negate
            if excluded:
                ignored.add(rel)
        return ignored

    @staticmethod
    def _git_ignored(root: Path, relpaths: list[str]) -> set[str]:
        if not relpaths:
            return set()
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "check-ignore", "--no-index", "-z", "--stdin"],
                input="\0".join(relpaths) + "\0", capture_output=True, text=True, timeout=10.0, check=False,
            )
            if result.returncode in (0, 1) and not result.stderr:
                return {value for value in result.stdout.split("\0") if value}
        except (OSError, subprocess.SubprocessError):
            pass
        return RepositoryIndexService._ignored_fallback(root, relpaths)

    @staticmethod
    def _candidate(rel: str, path: Path, stat: os.stat_result) -> bool:
        name = path.name.casefold()
        if any(part.casefold() in EXCLUDED_DIRS or part.casefold().endswith(".egg-info")
               or SECRET_NAME.search(part) or part.casefold() in SENSITIVE_BASENAMES for part in Path(rel).parts[:-1]):
            return False
        if name == ".agentmem-project.json" or name in SENSITIVE_BASENAMES:
            return False
        if SECRET_NAME.search(name) or path.suffix.casefold() in SECRET_SUFFIXES:
            return False
        if path.suffix.casefold() not in TEXT_SUFFIXES or stat.st_size > MAX_FILE_BYTES:
            return False
        return True

    def _metadata_scan(self, root: Path, limit: int) -> tuple[dict[str, tuple[Path, os.stat_result]], bool]:
        regular, truncated = self._iter_regular_files(root, limit)
        paths = [rel for rel, _, _ in regular]
        ignored = self._git_ignored(root, paths)
        candidates = {
            rel: (path, stat) for rel, path, stat in regular
            if rel not in ignored and self._candidate(rel, path, stat)
        }
        return candidates, truncated

    @staticmethod
    def _chunks(text: str) -> list[tuple[int, int, str]]:
        lines = text.splitlines()
        chunks: list[tuple[int, int, str]] = []
        start = 0
        while start < len(lines):
            end = min(len(lines), start + _CHUNK_LINES)
            while end > start + 1 and len("\n".join(lines[start:end])) > _CHUNK_CHARS:
                end -= 1
            body = "\n".join(lines[start:end])
            chunks.append((start + 1, max(start + 1, end), body))
            start = end
        if not chunks:
            chunks.append((1, 1, ""))
        return chunks

    def _read_changed(self, root: Path, rel: str, path: Path, before: os.stat_result) -> dict[str, Any] | None:
        try:
            if _reparse(path):
                return None
            current_path = path.resolve(strict=True)
            current_path.relative_to(root)
            ancestor = path.parent
            while ancestor != root:
                if _reparse(ancestor):
                    return None
                ancestor = ancestor.parent
            raw = path.read_bytes()
            after = path.stat(follow_symlinks=False)
            if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                return None
            if len(raw) > MAX_FILE_BYTES or b"\0" in raw:
                return {"relpath": rel, "excluded": True}
            text = raw.decode("utf-8")
            if SECRET_ASSIGNMENT.search(text):
                return {"relpath": rel, "excluded": True}
            return {"relpath": rel, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
                    "mtime_ns": after.st_mtime_ns, "chunks": self._chunks(text)}
        except UnicodeError:
            return {"relpath": rel, "excluded": True}
        except (OSError, ValueError):
            return None

    def _git_snapshot(self, root: Path) -> dict[str, Any]:
        return self._git_metadata(root)

    def _writer_lock(self, project_id: str, repo_id: str, timeout: float = 15.0) -> _ProcessLock:
        _, lock_dir = self._project_storage(project_id)
        key = hashlib.sha256((project_id + "\0" + repo_id).encode("utf-8")).hexdigest()
        lock = _ProcessLock(lock_dir / (key + ".writer.lock"))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if lock.acquire():
                return lock
            time.sleep(0.05)
        raise TimeoutError("Repository writer is busy; retry after the active sync completes")

    def resync(self, project_id: str, repository_id: str, max_files: int | None = None) -> dict[str, Any]:
        self.project_lookup(project_id)
        row = self._repo_row(project_id, repository_id)
        max_files = max(1, min(int(max_files or self.max_files), _MAX_SCAN_FILES))
        lock = self._writer_lock(project_id, repository_id)
        try:
            return self._sync(project_id, repository_id, Path(row["root"]), max_files, force=True)
        finally:
            lock.release()

    def _sync(self, project_id: str, repository_id: str, root: Path, max_files: int, *,
              watcher_owner: bool = False, force: bool = False,
              force_paths: set[str] | None = None) -> dict[str, Any]:
        started = time.time()
        candidates, truncated = self._metadata_scan(root, max_files)
        with self._connect(project_id) as db:
            old = {row["relpath"]: dict(row) for row in db.execute(
                "SELECT relpath,sha256,size_bytes,mtime_ns FROM repository_files WHERE project_id=? AND repository_id=?",
                (project_id, repository_id))}
        changed: list[dict[str, Any]] = []
        unstable: list[str] = []
        newly_excluded: list[str] = []
        for rel, (path, stat) in candidates.items():
            prior = old.get(rel)
            if (prior and prior["size_bytes"] == stat.st_size and prior["mtime_ns"] == stat.st_mtime_ns
                    and not force and rel not in (force_paths or set())):
                continue
            loaded = self._read_changed(root, rel, path, stat)
            if loaded is None:
                unstable.append(rel)
            elif loaded.get("excluded"):
                newly_excluded.append(rel)
            elif prior and prior["sha256"] == loaded["sha256"]:
                # Metadata-only touch: retain chunks/revision but update the file snapshot.
                loaded["unchanged_hash"] = True
                changed.append(loaded)
            else:
                changed.append(loaded)
        removed = []
        if not truncated:
            removed = list({rel for rel in old if rel not in candidates} | set(newly_excluded))
        content_changed = [item for item in changed if not item.get("unchanged_hash")]
        git = self._git_snapshot(root)
        now = time.time()
        with self._connect(project_id) as db:
            db.execute("BEGIN IMMEDIATE")
            for item in changed:
                if not item.get("unchanged_hash"):
                    db.execute("DELETE FROM repository_chunks WHERE project_id=? AND repository_id=? AND relpath=?",
                               (project_id, repository_id, item["relpath"]))
                db.execute("INSERT OR REPLACE INTO repository_files(project_id,repository_id,relpath,sha256,size_bytes,mtime_ns,indexed_at) VALUES(?,?,?,?,?,?,?)",
                           (project_id, repository_id, item["relpath"], item["sha256"], item["size_bytes"], item["mtime_ns"], now))
                if not item.get("unchanged_hash"):
                    db.executemany("INSERT INTO repository_chunks(project_id,repository_id,relpath,sha256,line_start,line_end,body) VALUES(?,?,?,?,?,?,?)",
                                   [(project_id, repository_id, item["relpath"], item["sha256"], start, end, body)
                                    for start, end, body in item["chunks"]])
            for rel in removed:
                db.execute("DELETE FROM repository_chunks WHERE project_id=? AND repository_id=? AND relpath=?", (project_id, repository_id, rel))
                db.execute("DELETE FROM repository_files WHERE project_id=? AND repository_id=? AND relpath=?", (project_id, repository_id, rel))
            row = db.execute("SELECT index_revision FROM repositories WHERE project_id=? AND repository_id=?", (project_id, repository_id)).fetchone()
            if not row:
                db.rollback()
                raise ValueError("Repository registration disappeared during sync")
            revision = int(row["index_revision"]) + int(bool(any(not item.get("unchanged_hash") for item in changed) or removed))
            watcher = db.execute("SELECT watcher_pid,watcher_heartbeat FROM repositories WHERE project_id=? AND repository_id=?", (project_id, repository_id)).fetchone()
            watcher_pid = os.getpid() if watcher_owner else int(watcher["watcher_pid"] or 0)
            watcher_heartbeat = now if watcher_owner else float(watcher["watcher_heartbeat"] or 0)
            db.execute("UPDATE repositories SET git_common_dir=?,branch=?,head_sha=?,dirty=?,index_revision=?,indexed_at=?,watcher_pid=?,watcher_heartbeat=?,pending_count=?,last_error='',last_scan_complete=?,updated_at=? WHERE project_id=? AND repository_id=?",
                       (git["git_common_dir"], git["branch"], git["head_sha"], int(git["dirty"]), revision, now,
                        watcher_pid, watcher_heartbeat, len(unstable), int(not truncated and not unstable), now, project_id, repository_id))
            db.commit()
        return {"project_id": project_id, "repository_id": repository_id, "index_revision": revision,
                "indexed_at": now, "files_changed": len(content_changed), "files_metadata_refreshed": len(changed) - len(content_changed),
                "files_deleted": len(removed),
                "pending_changes": len(unstable), "scan_truncated": truncated,
                "scan_complete": not truncated and not unstable, "duration_ms": int((time.time() - started) * 1000)}

    def _scan_diff(self, row: dict[str, Any]) -> tuple[int, bool]:
        root = Path(row["root"])
        candidates, truncated = self._metadata_scan(root, self.max_files)
        with self._connect(row["project_id"]) as db:
            old = {item["relpath"]: dict(item) for item in db.execute(
                "SELECT relpath,size_bytes,mtime_ns FROM repository_files WHERE project_id=? AND repository_id=?",
                (row["project_id"], row["repository_id"]))}
        diff = sum(1 for rel, (_, stat) in candidates.items()
                   if rel not in old or old[rel]["size_bytes"] != stat.st_size or old[rel]["mtime_ns"] != stat.st_mtime_ns)
        if not truncated:
            diff += sum(1 for rel in old if rel not in candidates)
        return diff, truncated

    def _set_watch_state(self, row: dict[str, Any], pending: int, error: str = "") -> None:
        now = time.time()
        with self._connect(row["project_id"]) as db:
            db.execute("UPDATE repositories SET watcher_pid=?,watcher_heartbeat=?,pending_count=?,last_error=?,updated_at=? WHERE project_id=? AND repository_id=?",
                       (os.getpid(), now, pending, error[:500], now, row["project_id"], row["repository_id"]))

    def _watch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                rows = self._repo_rows()
            except sqlite3.Error:
                self._stop.wait(self.poll_interval)
                continue
            current_ids = {(row["project_id"], row["repository_id"]) for row in rows}
            for repo_key in list(self._owners):
                if repo_key not in current_ids:
                    self._stop_observer(repo_key)
                    self._owners.pop(repo_key).release()
                    self._pending_since.pop(repo_key, None)
                    self._pending_paths.pop(repo_key, None)
                    self._pending_full.discard(repo_key)
                    self._last_reconcile.pop(repo_key, None)
            for row in rows:
                if self._stop.is_set():
                    break
                repo_id = row["repository_id"]
                repo_key = (row["project_id"], repo_id)
                owner = self._owners.get(repo_key)
                if owner is None:
                    _, lock_dir = self._project_storage(row["project_id"])
                    key = hashlib.sha256((row["project_id"] + "\0" + repo_id).encode("utf-8")).hexdigest()
                    owner = _ProcessLock(lock_dir / (key + ".watcher.lock"))
                    if owner.acquire():
                        self._owners[repo_key] = owner
                        self._pending_since[repo_key] = time.monotonic() - self.debounce_seconds
                        self._pending_full.add(repo_key)
                    else:
                        continue
                observer = self._observers.get(repo_key)
                if observer is None or not observer.is_alive():
                    if observer is not None:
                        self._stop_observer(repo_key)
                    try:
                        self._start_observer(row)
                        with self._mutex:
                            self._pending_full.add(repo_key)
                            self._pending_since.setdefault(repo_key, time.monotonic() - self.debounce_seconds)
                    except (OSError, RuntimeError, ValueError) as exc:
                        try:
                            self._set_watch_state(row, 1, str(exc))
                        except sqlite3.Error:
                            pass
                        continue
                try:
                    now_mono = time.monotonic()
                    with self._mutex:
                        pending_paths = set(self._pending_paths.get(repo_key, set()))
                        full_scan = repo_key in self._pending_full
                        pending = self._pending_since.get(repo_key, 0.0)
                    reconcile = now_mono - self._last_reconcile.get(repo_key, 0.0) >= self.reconcile_interval
                    diff, truncated = self._scan_diff(row) if full_scan or reconcile else (len(pending_paths), False)
                    if diff or truncated or full_scan:
                        if pending == 0.0:
                            pending = time.monotonic()
                            with self._mutex:
                                self._pending_since[repo_key] = pending
                        self._set_watch_state(row, diff, "")
                        if time.monotonic() - pending >= self.debounce_seconds:
                            with self._mutex:
                                pending_paths = set(self._pending_paths.pop(repo_key, set()))
                                full_scan = repo_key in self._pending_full
                                self._pending_full.discard(repo_key)
                                self._pending_since.pop(repo_key, None)
                            lock = self._writer_lock(row["project_id"], repo_id)
                            try:
                                force_all = full_scan and self._last_reconcile.get(repo_key, 0.0) == 0.0
                                result = self._sync(row["project_id"], repo_id, Path(row["root"]), self.max_files,
                                                    watcher_owner=True, force=force_all,
                                                    force_paths=pending_paths)
                            finally:
                                lock.release()
                            self._last_reconcile[repo_key] = time.monotonic()
                            if result["pending_changes"]:
                                with self._mutex:
                                    self._pending_since[repo_key] = time.monotonic()
                    else:
                        with self._mutex:
                            self._pending_since.pop(repo_key, None)
                        self._set_watch_state(row, 0, "")
                        if reconcile:
                            self._last_reconcile[repo_key] = now_mono
                except (OSError, sqlite3.Error, ValueError, TimeoutError, subprocess.SubprocessError) as exc:
                    try:
                        self._set_watch_state(row, 1, str(exc))
                    except sqlite3.Error:
                        pass
            self._stop.wait(self.poll_interval)

    def search(self, project_id: str, repository_id: str, query: str, limit: int = 5) -> dict[str, Any]:
        self.project_lookup(project_id)
        row = self._repo_row(project_id, repository_id)
        tokens = list(dict.fromkeys(_TOKEN.findall(query)))[:_MAX_QUERY_TOKENS]
        if not tokens:
            raise ValueError("query must contain at least one searchable token")
        match = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        limit = max(1, min(int(limit), _MAX_RESULTS))
        with self._connect(project_id) as db:
            rows = db.execute("""SELECT relpath,sha256,line_start,line_end,body,bm25(repository_chunks) AS score
                FROM repository_chunks WHERE repository_chunks MATCH ? AND project_id=? AND repository_id=?
                ORDER BY score LIMIT ?""", (match, project_id, repository_id, limit)).fetchall()
        results = []
        root = Path(row["root"])
        for item in rows:
            path = root / Path(item["relpath"])
            freshness = "source_unavailable"
            current_hash = None
            try:
                if not _reparse(path) and path.resolve(strict=True).is_relative_to(root):
                    raw = path.read_bytes()
                    current_hash = hashlib.sha256(raw).hexdigest()
                    freshness = "content_checked" if current_hash == item["sha256"] else "content_changed"
            except (OSError, ValueError):
                pass
            body = item["body"]
            folded = body.casefold()
            positions = [folded.find(token.casefold()) for token in tokens if folded.find(token.casefold()) >= 0]
            hit = min(positions) if positions else 0
            excerpt_start = max(0, min(hit - 1_200, max(0, len(body) - 2_400)))
            excerpt = body[excerpt_start:excerpt_start + 2_400]
            results.append({
                "citation": f"{item['relpath']}#L{item['line_start']}-L{item['line_end']}",
                "path": item["relpath"], "line_start": int(item["line_start"]), "line_end": int(item["line_end"]),
                "sha256": item["sha256"], "current_sha256": current_hash,
                "fresh": freshness == "content_checked", "freshness_level": freshness,
                "indexed_at": row["indexed_at"] or None, "excerpt": excerpt,
                "excerpt_char_start": excerpt_start,
                "excerpt_truncated": excerpt_start > 0 or excerpt_start + len(excerpt) < len(body),
            })
        status = self._public_status(row)
        gap = "No repository evidence matched. Check repository_status or repository_resync, then refine the query." if not results else ""
        return {"project_id": project_id, "repository_id": repository_id, "root": row["root"],
                "query": query, "results": results, "gap": gap,
                "index_revision": int(row["index_revision"]), "freshness": status["freshness"],
                "watcher_state": status["watcher_state"], "source_revision": row["head_sha"],
                "branch": row["branch"], "head_sha": row["head_sha"], "dirty": bool(row["dirty"]),
                "git_common_dir": row["git_common_dir"]}
