# Architecture and module boundary

Agent Memory is one reusable project-memory module inside a larger project-work system. It owns project-scoped evidence lookup and durable task/lesson records. It does not own the Codex model/runtime, project execution, hosting, scheduler, or a full dashboard.

```text
Codex / future manager runtime
      | MCP or CLI
      v
small locator registry
      | project_id -> source_root + knowledge_root
      v
per-project SQLite + curated Markdown under D:/projects/<project_id>
      |                          (authoritative intent/decisions)
      +-- source allowlist and incremental FTS index
      +-- projects, tasks/dependencies/leases/checkpoints
      +-- append-only receipts and lesson review/application
      ^
      | original project source roots remain where they are; indexed evidence stores paths, hashes, and excerpts
```

## Authority and records

- Each project has an independent AgentMem knowledge directory, default `D:/projects/<project_id>`. Its `PROJECT.md` is the editable manager brief; its `agentmem.sqlite3` is authoritative for task status, dependency edges, lease ownership, checkpoints, receipts, lesson review status, and the rebuildable evidence index. The MCP writes only this service-owned brief and never accepts arbitrary output paths.
- The app-level registry contains only `project_id`, source root, description, and knowledge-directory path. It routes each request to that project's database; all project content and working state lives in the corresponding per-project directory. `AGENTMEM_DATA_DIR` controls the registry and migration backups, while `AGENTMEM_MEMORY_ROOT` controls the default knowledge-directory parent.
- Before opening a project's database, AgentMem validates that the registered knowledge directory, project manifest, source-root binding, and database file still match the project and have not been replaced with a symlink/junction or moved path.
- Source repositories remain at their existing roots. Index records retain relative paths, SHA-256, line bounds, and excerpt text; they are not copies of the original repository. A citation beginning `@memory/` resolves under the project's KB directory, while other citations resolve under the registered source root.
- Every indexed source records a project ID, relative path, SHA-256, and index time. FTS chunks carry line bounds and source hash. Search returns excerpts and citations rather than unverified “facts.”
- Attach and task context perform a metadata-only scan of safe text paths (relative path, size, and modification time). It can flag changed/new/deleted paths but cannot verify content or find a change if timestamps and sizes are preserved; `index_project` hashes content and refreshes evidence.
- Project identity is an explicit ID. Optional cwd resolution succeeds only inside a registered root and chooses the most specific root. No process-wide current project is shared.
- SQLite `user_version` tracks schema versions; the current migration upgrades the original index table with metadata needed for fast freshness scans. Export uses a versioned JSON envelope.
- On upgrade, a WAL-consistent backup is kept under `<data_dir>/migration-backups`; each old project's records are copied into its own database and validated against the backup before the new locator registry becomes active. The original shared database stays in place as a retired recovery copy, with write guards on relational tables. Running pre-upgrade MCP processes must be restarted to use the new store.

## Task state

Task lifecycle: `open` → `in_progress` → `done`, or `cancelled`. Ready is derived: open, all dependencies done, and no explicit blocker. A blocked task remains open and reports dependency IDs or a blocker reason. Claims use a SQLite immediate transaction; one live lease owner holds a random token. Updates require the matching owner/token and an unexpired lease. Reclaim after expiry supports crash recovery. A receipt idempotency key prevents duplicate inserts; completion needs evidence for every acceptance check. Task records include a source revision and checkpoint so a resumed agent can inspect relevant changes. `task_context` bundles relevant evidence and accepted lessons for the claim.

Lessons begin as candidates. An owner reviews them as accepted, rejected, or superseded. Retrieval only returns accepted lessons inside the same project. A future shared lesson scope must be explicit; do not copy a project lesson across scopes implicitly.

## MCP tool catalog

| Tool | Purpose |
|---|---|
| `projects` | List/register/resolve source roots and isolated knowledge-directory locations |
| `attach_project` | One bounded manager context packet; optional `query` bundles focused cited evidence; `include_sources` opts into the detailed source manifest |
| `index_project` | Refresh project source snapshot and FTS index |
| `check_project_changes` | Metadata-only freshness scan; does not read file contents |
| `retrieve_evidence` | Lexical search with path, line, hash, freshness, and gap |
| `tasks` | Create, list, show, or edit unclaimed open work items |
| `claim_task` | Atomic ready-task lease acquisition |
| `task_context` | Bounded evidence/lesson/checkpoint packet for one task |
| `record_work` | Checkpoint, renew/release, or receipt under claim |
| `lessons` | Retrieve accepted, propose candidate, review, or show |
| `update_project_brief` | Write only `PROJECT.md` under the registered knowledge directory |
| `export_project` | Portable JSON export of task, receipt, dependency, and lesson state |

CLI commands call the same domain/store methods and do not duplicate memory logic. Future parent-system adapters can use Python service methods or add an API without changing the record model. Planned extension points include semantic retrieval, external source connectors, a dashboard, Postgres for multi-host writes, and a separately authorized job runner. None is required by the current local module.

## Current operational limits

The first version uses lexical FTS5 and manual re-indexing. It does not provide file watching, semantic search, automatic extraction/verification of all facts, concurrent multi-machine synchronization, remote hosting, an automatic Codex hook, or autonomous Codex scheduling. MCP tool schemas expose the capabilities; project instructions guide the agent but do not guarantee it will call every tool. Continue to cite evidence and surface missing/stale sources.

For common question answering, `attach_project(project_id, query=...)` combines orientation and up to three capped citation excerpts in one MCP call. This saves one agent/tool round trip when both operations are needed. The normal packet gives the indexed-source count and revision without listing every hash; cited evidence still carries its own source hash. Pass `include_sources=true` when the detailed manifest is needed. Larger or ambiguous questions should use `retrieve_evidence` for more results. Local tool latency does not measure model reasoning or end-to-end answer time.

