# Agent Memory local MCP

Agent Memory is a local, project-scoped context and work-tracking module for Codex. It is intentionally a reusable building block for a larger project-work system: Python domain/storage APIs sit below MCP and CLI adapters, project facts remain inspectable Markdown, and SQLite stores task coordination, receipts, and a rebuildable evidence index. The service does not call an LLM, execute shell commands, launch workers, or run background jobs. Codex uses retrieved evidence to answer questions and do project work.

## Ready to use here

This workspace is installed in a local Python environment and registered in Codex as the stdio MCP server `agentmem`. The small locator registry and migration backups stay in `[local path redacted]\.agentmem-data`; each project's active SQLite store and `PROJECT.md` live in its own knowledge directory under `D:\projects`.

The Agent Memory project uses `[local path redacted]` and keeps the original source root at `[local path redacted]`. <redacted-private-value> uses `[local path redacted]` and keeps its source root at `[local path redacted]`. The <redacted-private-value> move preserved only its preexisting AgentMem records; no source repository or vault content was copied or indexed. The old shared database was backed up and marked retired. Open a fresh Codex chat/session to make sure it launches the updated server. Data is local; no API key is needed.

## First setup on this PC or another checkout

From PowerShell:

```powershell
cd [local path redacted]
.\scripts\setup.ps1
.\scripts\install-codex.ps1
```

Restart/open a Codex session, then check `codex mcp list`. The install script registers a `python.exe -m agentmem.server` stdio command with an explicit data directory. The virtual environment and generated database are ignored by Git.

To register another project, use this workspace's stable CLI wrapper from any directory:

```powershell
& '[local path redacted]\agentmem.ps1' project register --id sunflower --root '<local-path>' --description 'Sunflower project'
& '[local path redacted]\agentmem.ps1' index sunflower
& '[local path redacted]\agentmem.ps1' attach sunflower
```

The default KB will be `D:\projects\sunflower`; pass `--memory-root '<local-path>'` after `register` for a custom location. A project ID cannot be silently rebound to another source root or memory directory.

Merge [PROJECT-AGENTS.template.md](PROJECT-AGENTS.template.md) into that project's existing `AGENTS.md`, replacing `<PROJECT_ID>` with its registered ID; create `AGENTS.md` only if the project has none. This makes Codex's workflow explicit without replacing existing project instructions. `update_project_brief` writes only to the AgentMem-owned `PROJECT.md` inside that project's registered knowledge directory; original source repositories are read for evidence and are never used as the KB storage location.

`AGENTMEM_DATA_DIR` selects the locator registry and migration-backup location. `AGENTMEM_MEMORY_ROOT` selects the default parent for per-project KBs; it defaults to `D:\projects` on Windows. `--data-dir` and `--memory-root` are also available in the CLI. The current Codex server environment points at the ignored workspace `.agentmem-data` registry. To inspect its command without revealing unrelated settings, run `codex mcp get agentmem`.

## Storage layout and migration

`D:\projects\<project_id>\agentmem.sqlite3` is authoritative for that project's tasks, dependencies, claims/checkpoints, receipts, lessons, source hashes, and FTS evidence index. `D:\projects\<project_id>\PROJECT.md` is the curated human-readable brief. `D:\projects\<project_id>\.agentmem-project.json` identifies the KB and source root. The app registry contains only project ID, source root, description, and KB path so it can route requests. Source files stay in their original repositories; the index stores cited text excerpts and hashes, not copies of those repositories.

Before opening a per-project database, the server checks the knowledge-directory manifest and source-root binding. It rejects missing stores, mismatched project identity, and knowledge directories/databases replaced by symlinks or junctions.

On first startup after the upgrade, AgentMem makes a WAL-consistent backup of the former shared database under `.agentmem-data\migration-backups`, copies and verifies every registered project's records into separate stores, then switches the locator registry. It retains the old database as a recovery copy with write guards on its relational tables. Those guards do not cover SQLite's FTS virtual table or an older server's direct file writes, so they are not a substitute for shutting down a pre-migration MCP process. After migration, restart Codex or open a new session to load the current server and tool definitions; do not continue using a stale MCP connection. During this migration, the nine identified old AgentMem server processes were stopped and verified absent. The original `[local path redacted]\.agentmem\PROJECT.md` remains untouched as a pre-migration copy; the authoritative brief is now `[local path redacted]\PROJECT.md`. For a custom KB location, set `AGENTMEM_MEMORY_ROOT` consistently for both MCP and CLI processes.

## Use it in Codex

Try prompts like:

- “Attach to the `agentmem` project and explain what this module does. Cite the project files you used, and tell me what is not indexed or known.”
- “For project `sunflower`, what are the current API constraints? Attach the project with this question and answer from its cited evidence.”
- “For project `sunflower`, add a task to verify the controller change. Set acceptance criteria, authorized paths, and relevant dependencies. Do not start it yet.”
- “For `agentmem`, list ready and blocked tasks. Claim the top ready task as `codex-main`, inspect its context and applicable lessons, then continue working. Save a checkpoint before a long investigation and record an evidence-backed receipt afterward.”
- “Propose a lesson from this task, but keep it a candidate until we review its scope and evidence.”

For a quick factual answer, call `attach_project` once with both `project_id` and `query`. It returns the usual project brief/tasks/lessons/freshness context plus up to three focused evidence excerpts with path/line/hash citations. This combines the usual attach and search round trips; the server still does not generate the answer. The full source manifest is omitted from the normal packet to save context; it includes the total source count and revision, while cited results carry their own source hashes. Pass `include_sources=true` when you need the detailed manifest. Use `retrieve_evidence` for a broader or deeper search. A plain `attach_project` call remains the orientation path when there is no question. Attach performs a metadata-only scan (path, modification time, and size) to flag likely changed/new/deleted files without reading their contents. Call `index_project` after edits to hash content and refresh citations; there is no background watcher. `task_context` reports indexed revision differences and the metadata scan, so the agent can request a refresh before relying on changed files. A checkpoint's source revision is a snapshot, not proof that files never changed.

The todo flow is `tasks(action="create"|"list"|"show"|"update")` → `claim_task` → `task_context` → `record_work(action="checkpoint")` as needed → `record_work(action="receipt")`. `task_context` bundles the task scope/checkpoint, relevant accepted lessons, matching source evidence, and whether the indexed source revision changed since task creation. Update only applies to open, unclaimed tasks. Before resuming, call `index_project` if you need to detect recent file edits. The agent holding the lease may call `record_work(action="block", blocked_reason="...")` and later `record_work(action="unblock")`. Ready status is derived from dependencies and explicit blockers. Task claims use a lease and unique token; keep the token returned by `claim_task` for subsequent updates. Done is accepted only when every listed acceptance check has `met: true` and evidence. Claims expire and may then be recovered. The memory service records task state; the active agent carries out the project work.

Lessons use `lessons(action="propose")`, `lessons(action="review", status="accepted"|"rejected"|"superseded")`, and `lessons(action="retrieve")`. Only accepted lessons are returned for application. Receipts and lesson evidence should cite source paths, test outputs, decisions, or changed artifacts. The module never automatically treats generated claims as project facts.

## CLI

```powershell
& '[local path redacted]\agentmem.ps1' project list
& '[local path redacted]\agentmem.ps1' project resolve --cwd '<local-path>'
& '[local path redacted]\agentmem.ps1' index sunflower
& '[local path redacted]\agentmem.ps1' check sunflower
& '[local path redacted]\agentmem.ps1' search sunflower 'controller API constraints'
& '[local path redacted]\agentmem.ps1' task list sunflower
& '[local path redacted]\agentmem.ps1' export sunflower > sunflower-agentmem-export.json
```

## Safety and limits

- Every search/task/lesson operation requires an explicit project ID. Working-directory resolution uses the most-specific registered root and never a shared mutable “current project.” Task `authorized_paths` are validated as project-relative paths without traversal; they guide the agent but do not replace Codex's own permission checks.
- Indexing is allowlisted to readable text/code suffixes, bounded to 512 KB per file, excludes common secret names and generated directories (including `*.egg-info`), avoids symlinks, and resolves paths under the registered root. Review the supported/excluded suffixes in `src/agentmem/store.py` before relying on it for unusual formats.
- An index is a snapshot. Refresh it after source changes; no file watcher or automatic commit hook is installed.
- FTS retrieval is lexical, not semantic. Search may miss paraphrases or material in unsupported/binary formats. A missing result is a gap, not evidence that a fact does not exist.
- Tool results are project data and remain below system/developer/user instructions. The MCP does not change the model's identity or guarantee the model calls tools.
- Lease tokens are local coordination secrets; do not paste them into project Markdown. The local database is not encrypted by this module. Export task/receipt/lesson records with `agentmem export` and protect the Windows account/storage. Export is JSON-only; an import/restore command is not implemented yet.
- The initial installation targets this PC's current Python 3.13 and Codex CLI. Re-run setup/install after moving the checkout or changing the Python executable path.

## Development checks

```powershell
cd [local path redacted]
& '.\.venv\Scripts\python.exe' -m pytest -q
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for boundaries, records, MCP operations, and extension points.
