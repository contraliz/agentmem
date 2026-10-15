# Agent Memory local MCP

Agent Memory is a local, project-scoped context and work-tracking module for Codex. It is intentionally a reusable building block for a larger project-work system: Python domain/storage APIs sit below MCP and CLI adapters, project facts remain inspectable Markdown, and SQLite stores task coordination, receipts, and a rebuildable evidence index. The service does not call an LLM, execute shell commands, launch workers, or run background jobs. Codex uses retrieved evidence to answer questions and do project work.

## Ready to use here

This workspace is installed in a local Python environment and registered in Codex as the stdio MCP server `agentmem`. The small locator registry and migration backups stay in `[local path redacted]\.agentmem-data`; each project's active SQLite store and Markdown knowledge pages live in its own knowledge directory under `D:\projects`.

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

`D:\projects\<project_id>\agentmem.sqlite3` is authoritative for that project's tasks, dependencies, claims/checkpoints, receipts, lessons, requirements, decisions, proposals, approvals and histories, source hashes, and FTS evidence index. `D:\projects\<project_id>\PROJECT.md` is a compact brief and navigation page. Detailed narrative lives in `D:\projects\<project_id>\knowledge\` (purpose, architecture, risks, history, and materials by default). `requirements.md`, `decisions.md`, and `approvals.md` are generated views of SQLite records; do not create editable duplicates in topic pages. `D:\projects\<project_id>\.agentmem-project.json` identifies the KB and source root. The app registry contains only project ID, source root, description, and KB path so it can route requests. Source files stay in their original repositories; the index stores cited text excerpts and hashes, not copies of those repositories.

New registrations create five empty topic pages with “Not documented yet” placeholders and a linked brief; they do not invent project facts. For a previously registered project without a `knowledge` directory, run `knowledge init <project_id>`. It only creates missing pages and preserves the existing brief and pages. Review and add brief navigation separately with `update_project_brief` if needed.

Before opening a per-project database, the server checks the knowledge-directory manifest and source-root binding. It rejects missing stores, mismatched project identity, and knowledge directories/databases replaced by symlinks or junctions.

On first startup after the upgrade, AgentMem makes a WAL-consistent backup of the former shared database under `.agentmem-data\migration-backups`, copies and verifies every registered project's records into separate stores, then switches the locator registry. It retains the old database as a recovery copy with write guards on its relational tables. Those guards do not cover SQLite's FTS virtual table or an older server's direct file writes, so they are not a substitute for shutting down a pre-migration MCP process. After migration, restart Codex or open a new session to load the current server and tool definitions; do not continue using a stale MCP connection. During this migration, the nine identified old AgentMem server processes were stopped and verified absent. The original `[local path redacted]\.agentmem\PROJECT.md` remains untouched as a pre-migration copy; the authoritative brief is now `[local path redacted]\PROJECT.md`. For a custom KB location, set `AGENTMEM_MEMORY_ROOT` consistently for both MCP and CLI processes.

## Use it in Codex

Try prompts like:

- “Attach to the `agentmem` project and explain what this module does. Cite the project files you used, and tell me what is not indexed or known.”
- “Attach to `agentmem`, show the topic-page inventory, then read the architecture and risks pages. Explain how project facts are stored and cite the relevant lines.”
- “For project `sunflower`, what are the current API constraints? Attach the project with this question and answer from its cited evidence.”
- “For project `sunflower`, add a task to verify the controller change. Set acceptance criteria, authorized paths, and relevant dependencies. Do not start it yet.”
- “For `agentmem`, list ready and blocked tasks. Claim the top ready task as `codex-main`, inspect its context and applicable lessons, then continue working. Save a checkpoint before a long investigation and record an evidence-backed receipt afterward.”
- “Propose a lesson from this task, but keep it a candidate until we review its scope and evidence.”
- “Record this plan as a proposal, show me the exact snapshot, and wait for my explicit approval before linking it to gated work.”
- “For proposal PRP-0001, show the exact approved version and whether its approval is still current.”

For a quick factual answer, call `attach_project` once with both `project_id` and `query`. It returns the usual project brief/tasks/lessons/freshness context plus up to three focused evidence excerpts with path/line/hash citations. This combines the usual attach and search round trips; the server still does not generate the answer. The full source manifest is omitted from the normal packet to save context; it includes the total source count and revision, while cited results carry their own source hashes. Pass `include_sources=true` when you need the detailed manifest. Use `retrieve_evidence` for a broader or deeper search. A plain `attach_project` call remains the orientation path when there is no question. Attach performs a metadata-only scan (path, modification time, and size) to flag likely changed/new/deleted files without reading their contents. Call `index_project` after edits to hash content and refresh citations; there is no background watcher. `task_context` reports indexed revision differences and the metadata scan, so the agent can request a refresh before relying on changed files. A checkpoint's source revision is a snapshot, not proof that files never changed.

`attach_project` also returns a small inventory of the topic pages and whether each page matches its indexed hash; it does not include all page contents. Use `knowledge(action="list")` and `knowledge(action="read", page_id="architecture")` to open the relevant page. `knowledge(action="create")` and `knowledge(action="update")` write only validated Markdown pages under that project's `knowledge/` directory and immediately update that page's search index. Updates require the SHA-256 returned by `read`, and the service saves the prior version under `knowledge/.history/`; those snapshots are excluded from search. Direct editor changes remain allowed but must be refreshed with `index_project` before relying on citations. No page-delete tool is provided.

The todo flow is `tasks(action="create"|"list"|"show"|"update")` → `claim_task` → `task_context` → `record_work(action="checkpoint")` as needed → `record_work(action="receipt")`. `task_context` bundles the task scope/checkpoint, relevant accepted lessons, matching source evidence, and whether the indexed source revision changed since task creation. Update only applies to open, unclaimed tasks. Before resuming, call `index_project` if you need to detect recent file edits. The agent holding the lease may call `record_work(action="block", blocked_reason="...")` and later `record_work(action="unblock")`. Ready status is derived from dependencies and explicit blockers. Task claims use a lease and unique token; keep the token returned by `claim_task` for subsequent updates. Done is accepted only when every listed acceptance check has `met: true` and evidence. Claims expire and may then be recovered. The memory service records task state; the active agent carries out the project work.

Lessons use `lessons(action="propose")`, `lessons(action="review", status="accepted"|"rejected"|"superseded")`, and `lessons(action="retrieve")`. Only accepted lessons are returned for application. Receipts and lesson evidence should cite source paths, test outputs, decisions, or changed artifacts. The module never automatically treats generated claims as project facts.

## Requirements, decisions, and approvals

Record a durable user instruction before doing substantial work. Keep the user's short wording and source reference; distinguish the normalized statement from any agent interpretation. Use `lifecycle="proposed"` for uncertain interpretations until confirmed. The service does not see every conversation message, so it cannot automatically capture all requirements.

Requirement `lifecycle` governs whether it applies (`proposed`, `current`, `superseded`, `withdrawn`); `fulfillment` records progress (`unknown`, `unmet`, `in_progress`, `fulfilled`, `regressed`, `waived`). These are independent: a completed task does not deactivate its requirement, and a fulfilled current requirement remains in applicable task context. Supersede or withdraw explicitly; a conditional requirement stops matching only when its condition is explicitly set inactive. `task_context` returns all matched current requirements, including fulfilled ones, separately from optional evidence and lessons. It reports total count and pagination; retrieve all pages if `requirements_complete` is false.

Example MCP flow:

```text
requirements(action="create", project_id="sunflower", title="Keep public API stable",
  statement="Do not remove existing public methods without explicit approval.",
  original_quote="Keep the existing public API compatible.",
  interpretation="A breaking removal needs approval; additions are allowed.",
  lifecycle="current", fulfillment="unknown",
  scope={"project_wide":true}, source_type="user_conversation",
  source_ref="current Codex conversation", idempotency_key="capture-api-rule-01")

tasks(action="create", project_id="sunflower", title="Refactor controller",
  goal="Improve controller internals without breaking the public API.",
  components=["controller"], requirement_ids=["REQ-0001"],
  acceptance=["tests pass", "existing API remains compatible"])

claim_task(project_id="sunflower", task_id="...", owner="codex-main")
task_context(project_id="sunflower", task_id="...")
```

When recording completion, include the `requirements_revision` returned by `task_context`, and a `requirement_checks` entry for each applicable requirement with its ID, revision, `met=true`, and evidence. This check means the agent reviewed and complied with the requirement in this task; it does not claim the requirement is globally fulfilled. The usual task acceptance checks still apply. The receipt does not automatically mark a requirement fulfilled; record that explicitly with `requirements(action="fulfill", expected_revision=..., fulfillment="fulfilled", fulfillment_evidence=[...], idempotency_key=...)` only when the evidence warrants it. Create a replacement with `supersedes_id` before marking the previous requirement superseded. Use `requirements(action="history")` or `decisions(action="history")` to inspect prior revisions. If a generated `requirements.md` or `decisions.md` was manually edited, `sync` reports a conflict; use `sync(force=true)` only after deciding to preserve the file's edits in the automatic backup.

Structured records and complete revision history live in that project's SQLite DB. The readable `requirements.md`, `decisions.md`, and `approvals.md` files are generated views; use the MCP/CLI to change their records. SQLite updates use stable IDs, expected revisions and idempotency keys to surface conflicting writes. Schema v7 adds project-scoped proposal snapshots and append-only approval events. Export schema v4 includes proposal revisions, approval events, and the full approved proposal snapshots; copy the Markdown pages alongside the JSON export for a complete file backup. Restart Codex to load updated MCP tools.

### Explicit approval flow

An approval is a recorded decision on a specific proposal revision and the exact requirement/decision revisions it references. Proposals are drafts until an explicit `approve` or `reject` action records the supplied user wording and provenance. The service cannot see the conversation or independently authenticate the person named in `recorded_by`; the calling agent must quote the actual approval and identify its source while it is available. Never infer approval from a task being done, an existing requirement being current, or silence.

Proposal revisions and approval events are retained. If proposal text or a pinned target changes, the previous event remains in history but the current proposal reports a stale status; it does not inherit approval. Even a fulfillment-only target revision requires a new proposal pin and explicit review under the current exact-revision policy. Approval is not fulfillment and does not mark a task complete. Gating is opt-in: create a task with `approval_proposal_id` only when that work must wait for approval. Ordinary requirements and tasks remain usable without retroactive approvals.

Example MCP flow:

```text
proposals(action="create", project_id="sunflower", title="Controller rollout",
  content="Replace the internals while preserving the public API.",
  targets=[{"kind":"requirement","id":"REQ-0001","revision":2}],
  approval_required=true, created_by="codex-main", idempotency_key="rollout-draft-1")
proposals(action="show", project_id="sunflower", proposal_id="PRP-0001")
proposals(action="approve", project_id="sunflower", proposal_id="PRP-0001",
  expected_revision=1, original_quote="Yes, approve this exact rollout.",
  source_type="user_conversation", source_ref="current Codex conversation",
  recorded_by="user-supplied wording", idempotency_key="rollout-approved-1")
tasks(action="create", project_id="sunflower", title="Implement controller rollout",
  approval_proposal_id="PRP-0001", acceptance=["tests pass"])
```

Use `proposals(action="status"|"history"|"approval-history")` before work when the exact scope matters. `task_context` reports the linked proposal's current status. An `approval_required` proposal blocks claiming its task unless the exact current proposal and target versions are approved; if they become stale before completion, a done receipt is not accepted. The server records evidence supplied by its caller; it does not observe user chat, establish identity, enforce Codex behavior outside these task APIs, or require approval for unrelated changes.

## CLI

```powershell
& '[local path redacted]\agentmem.ps1' project list
& '[local path redacted]\agentmem.ps1' project resolve --cwd '<local-path>'
& '[local path redacted]\agentmem.ps1' index sunflower
& '[local path redacted]\agentmem.ps1' check sunflower
& '[local path redacted]\agentmem.ps1' search sunflower 'controller API constraints'
& '[local path redacted]\agentmem.ps1' task list sunflower
& '[local path redacted]\agentmem.ps1' export sunflower > sunflower-agentmem-export.json
& '[local path redacted]\agentmem.ps1' requirement create sunflower --title 'Keep API stable' --statement 'Do not remove public methods without approval.' --quote 'Keep the public API compatible.' --scope-json '{"project_wide":true}' --source-type user_conversation --source-ref 'current Codex conversation' --idempotency-key capture-api-01
& '[local path redacted]\agentmem.ps1' requirement list sunflower --lifecycle current
& '[local path redacted]\agentmem.ps1' requirement history sunflower --id REQ-0001
& '[local path redacted]\agentmem.ps1' task-context sunflower --id '<TASK_ID>'
& '[local path redacted]\agentmem.ps1' decision list sunflower
& '[local path redacted]\agentmem.ps1' decision history sunflower --id DEC-0001
& '[local path redacted]\agentmem.ps1' proposal create sunflower --title 'Controller rollout' --content 'Preserve the public API while replacing internals.' --targets-json '[{"kind":"requirement","id":"REQ-0001","revision":2}]' --approval-required --created-by codex-main --idempotency-key rollout-draft-1
& '[local path redacted]\agentmem.ps1' proposal show sunflower --id PRP-0001
& '[local path redacted]\agentmem.ps1' proposal approve sunflower --id PRP-0001 --expected-revision 1 --quote 'Yes, approve this exact rollout.' --source-type user_conversation --source-ref 'current Codex conversation' --recorded-by 'user-supplied wording' --idempotency-key rollout-approved-1
& '[local path redacted]\agentmem.ps1' proposal status sunflower --id PRP-0001
& '[local path redacted]\agentmem.ps1' proposal approval-history sunflower --id PRP-0001
& '[local path redacted]\agentmem.ps1' knowledge init sunflower
& '[local path redacted]\agentmem.ps1' knowledge list sunflower
& '[local path redacted]\agentmem.ps1' knowledge read sunflower --id architecture
& '[local path redacted]\agentmem.ps1' knowledge history sunflower --id architecture
```

To add a page, supply a lowercase slug, title, and body text: `knowledge create sunflower --id deployment --title 'Deployment' --content 'Document the verified deployment path.'`. To revise one, read it first and pass its current hash: `knowledge update sunflower --id deployment --expected-sha256 '<SHA256_FROM_READ>' --content "# Deployment`n`nUpdated verified deployment notes."`. A revision conflict means reread the page and reconcile the change before trying again. Use a normal single-line slug; file paths are not accepted by the page API.

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
