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

- Each project has an independent AgentMem knowledge directory, default `D:/projects/<project_id>`. Its `PROJECT.md` is the editable manager brief; its `agentmem.sqlite3` is authoritative for task status, dependency edges, lease ownership, checkpoints, receipts, lesson review status, requirements, decisions and their revision histories, and the rebuildable evidence index. `requirements.md` and `decisions.md` are generated projections from SQLite, not independent editable authorities; the service detects manual edits and preserves them as a conflict until an explicit forced regeneration makes a backup. The MCP writes only these service-owned files and never accepts arbitrary output paths.
- The app-level registry contains only `project_id`, source root, description, and knowledge-directory path. It routes each request to that project's database; all project content and working state lives in the corresponding per-project directory. `AGENTMEM_DATA_DIR` controls the registry and migration backups, while `AGENTMEM_MEMORY_ROOT` controls the default knowledge-directory parent.
- Before opening a project's database, AgentMem validates that the registered knowledge directory, project manifest, source-root binding, and database file still match the project and have not been replaced with a symlink/junction or moved path.
- Source repositories remain at their existing roots. Index records retain relative paths, SHA-256, line bounds, and excerpt text; they are not copies of the original repository. A citation beginning `@memory/` resolves under the project's KB directory, while other citations resolve under the registered source root.
- Every indexed source records a project ID, relative path, SHA-256, and index time. FTS chunks carry line bounds and source hash. Search returns excerpts and citations rather than unverified “facts.”
- Attach and task context perform a metadata-only scan of safe text paths (relative path, size, and modification time). It can flag changed/new/deleted paths but cannot verify content or find a change if timestamps and sizes are preserved; `index_project` hashes content and refreshes evidence.
- Project identity is an explicit ID. Optional cwd resolution succeeds only inside a registered root and chooses the most specific root. No process-wide current project is shared.
- SQLite `user_version` tracks schema versions; schema v3 added source freshness metadata and task components, schema v4 added independent requirement/decision records, revision histories, links, and projection hashes, and schema v5 adds a database guard requiring the current requirements revision in a completion receipt. An older still-running MCP process therefore cannot silently finalize a task without that revision. Restart Codex to load new tool definitions and scoped requirement checks. Export uses a versioned JSON envelope (v2 includes requirement/decision histories).
- On upgrade, a WAL-consistent backup is kept under `<data_dir>/migration-backups`; each old project's records are copied into its own database and validated against the backup before the new locator registry becomes active. The original shared database stays in place as a retired recovery copy, with write guards on relational tables. SQLite FTS virtual tables cannot use those triggers, and older server versions may write the old brief file directly, so the guards alone do not make an already-running old process safe. Stop pre-upgrade MCP processes and restart/open a new Codex session to load the current server and tool definitions before continuing work.

## Task state

Task lifecycle: `open` → `in_progress` → `done`, or `cancelled`. Ready is derived: open, all dependencies done, and no explicit blocker. A blocked task remains open and reports dependency IDs or a blocker reason. Claims use a SQLite immediate transaction; one live lease owner holds a random token. Updates require the matching owner/token and an unexpired lease. Reclaim after expiry supports crash recovery. A receipt idempotency key prevents duplicate inserts; completion needs evidence for every acceptance check. Task records include a source revision and checkpoint so a resumed agent can inspect relevant changes. `task_context` bundles relevant evidence and accepted lessons for the claim.

Lessons begin as candidates. An owner reviews them as accepted, rejected, or superseded. Retrieval only returns accepted lessons inside the same project. A future shared lesson scope must be explicit; do not copy a project lesson across scopes implicitly.

## Requirements and decisions

Requirements have two independent axes. `lifecycle` (`proposed`, `current`, `superseded`, `withdrawn`) determines whether a statement governs work. `fulfillment` (`unknown`, `unmet`, `in_progress`, `fulfilled`, `regressed`, `waived`) describes whether it is satisfied. A fulfilled requirement remains current and applicable; completing a task never retires it. Only explicit supersession, withdrawal, or an explicitly inactive applicability condition removes it from current task context. Unknown conditions are included and flagged for the agent to assess.

Each requirement has a stable project-scoped ID, type, statement, optional exact user wording, separately labeled interpretation, applicability (project-wide, named components, relative path globs, or a condition), criteria, fulfillment evidence, provenance and current revision. `proposed` interpretations are surfaced separately and never silently become mandatory. Decisions record the chosen option, rationale, alternatives, scope, source wording/provenance, revisit condition, affected requirement IDs and revision history. SQLite holds authoritative records and append-only snapshots; `requirements.md` / `decisions.md` are generated human-readable views. Writes require an idempotency key; updates require an expected revision to detect races. A manual projection edit is preserved; `sync` without force reports the conflict, while explicit `sync(force=true)` backs up the edited file before regeneration.

`requirements(action=...)` and `decisions(action=...)` create, list, show and inspect history; requirements can be updated, fulfilled, withdrawn or superseded. Create a replacement requirement with `supersedes_id=<old id>` before marking the old one superseded. Tasks can link stable requirement IDs and label components. `task_context` returns every current requirement that is project-wide, linked to the task, or matches its declared component/authorized paths; it includes fulfilled requirements and reports condition uncertainty, stale links, record revision and pagination/completeness. It keeps proposed matches in a separate list. If a task requests `done`, its receipt must include the current requirements revision and an evidence-backed check for each applicable requirement in addition to normal acceptance checks. A requirement check means the agent reviewed and complied with the requirement for this task; it does not mean the requirement is globally fulfilled. This does not automatically mark any requirement fulfilled; use an explicit `requirements(action="fulfill")` update with evidence. Existing historical receipts are not retroactively certified.

The server does not receive every user message, so the calling agent must explicitly capture important user constraints and corrections before acting. Keep the source quote and mark uncertain agent interpretation as `proposed`. The service stores records; it cannot guarantee they were captured or followed. On restart, reattach and call `task_context` again rather than relying on chat history. The record ledger is intentionally project scoped; no other project's requirements are copied.

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
| `requirements` | Record lifecycle, applicability, and fulfillment independently; show history and generated projection |
| `decisions` | Record project decisions, rationale, links to requirements, and history |
| `update_project_brief` | Write only `PROJECT.md` under the registered knowledge directory |
| `export_project` | Portable versioned JSON export of task, receipt, dependency, lesson, requirement, decision, and history state |

CLI commands call the same domain/store methods and do not duplicate memory logic. Future parent-system adapters can use Python service methods or add an API without changing the record model. Planned extension points include semantic retrieval, external source connectors, a dashboard, Postgres for multi-host writes, and a separately authorized job runner. None is required by the current local module.

## Current operational limits

The first version uses lexical FTS5 and manual re-indexing. It does not provide file watching, semantic search, automatic extraction/verification of all facts, concurrent multi-machine synchronization, remote hosting, an automatic Codex hook, or autonomous Codex scheduling. MCP tool schemas expose the capabilities; project instructions guide the agent but do not guarantee it will call every tool. Continue to cite evidence and surface missing/stale sources.

For common question answering, `attach_project(project_id, query=...)` combines orientation and up to three capped citation excerpts in one MCP call. This saves one agent/tool round trip when both operations are needed. The normal packet gives the indexed-source count and revision without listing every hash; cited evidence still carries its own source hash. Pass `include_sources=true` when the detailed manifest is needed. Larger or ambiguous questions should use `retrieve_evidence` for more results. Local tool latency does not measure model reasoning or end-to-end answer time.

