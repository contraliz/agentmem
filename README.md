# AgentMem

AgentMem is a local, project-scoped context and work-tracking module for Codex. Its Python domain and storage layer sit below MCP and CLI adapters. It keeps authored project facts in inspectable Markdown and structured work records in SQLite. It does not call an LLM, execute shell commands, launch workers, or schedule background jobs.

## Install a wheel

Use Python 3.11 or newer. After downloading a wheel from the project release page, install it in the Python environment that will run the MCP server:

```console
python -m pip install ./agentmem_local-0.1.0-py3-none-any.whl
agentmem --help
```

To install from a source archive, unpack it and run:

```console
python -m pip install ./agentmem_local-0.1.0.tar.gz
```

For development from a repository checkout, run `python -m pip install .` at its root. The optional PowerShell setup script creates a local virtual environment for development.

AgentMem stores data locally. No API key is needed. Set `AGENTMEM_DATA_DIR` to choose the locator registry and migration-backup folder, and `AGENTMEM_MEMORY_ROOT` to choose where new project knowledge directories are created. By default the registry is under the current user's home directory, and the memory root follows the platform default. Existing projects keep their registered locations; changing the default does not move or migrate their data.

## Register AgentMem in Codex

In PowerShell, register the installed module as a stdio MCP server:

```powershell
codex mcp add agentmem `
  --env "AGENTMEM_DATA_DIR=$env:LOCALAPPDATA\AgentMem\data" `
  --env "AGENTMEM_MEMORY_ROOT=$env:USERPROFILE\AgentMem\projects" `
  -- python -m agentmem.server
codex mcp list
```

Use the Python interpreter where the wheel was installed if `python` resolves to a different environment. Restart or open a Codex session to load the server and current tool definitions.

After installation, the `agentmem` command works from any current directory:

```console
agentmem project register --id example-project --root <path-to-project> --description "Example project"
agentmem index example-project
agentmem attach example-project
```

Pass `--memory-root <path>` to `project register` to choose that project's knowledge directory. A project ID cannot be silently rebound to another source root or memory directory. See [DISTRIBUTION.md](DISTRIBUTION.md) for local artifact builds and current release readiness.

When an agent starts a new KB through the MCP, it should first confirm the exact project ID and source root (or resolve a registered working directory), then call `projects(action="register")`. The registration result includes an `onboarding` object: a new project is `scaffold_only`, with `curation_status="not_started"` and next steps; a repeated registration is reported as `existing_registration` and asks the agent to inspect current context before doing setup again. Registration creates the database, manifest, linked brief, empty topic pages, and generated record views. It does not index source files or establish project facts. Follow the returned steps: call `index_project`, inspect `attach_project` and cited evidence, curate `PROJECT.md` and focused pages, then report what is verified and what remains unknown. Existing project identity or knowledge-directory conflicts fail; no KB is adopted or overwritten.

If the project owner wants persistent project-specific Codex guidance, review [PROJECT-AGENTS.template.md](PROJECT-AGENTS.template.md) and merge suitable instructions into that project's existing `AGENTS.md`; do not overwrite existing instructions. `update_project_brief` writes only to the AgentMem-owned `PROJECT.md` inside that project's registered knowledge directory; original source repositories are read for evidence and are never used as the KB storage location.

## Project recording reminder

`attach_project` returns a concise `recording_reminder` and `end_of_pass_summary_reminder`. The agent should briefly name knowledge pages or entries it saved or changed at the end of its work pass, or state that there were no project knowledge changes. Repository indexing is code indexing, not knowledge ingestion. These are agent-facing instructions: AgentMem does not detect every pass or guarantee a user-visible summary or knowledge write. The former wrapper prompt commands and pass-boundary mechanism remain removed; no hook integration is active.

## Repository indexing service

The repository-indexing service is a distinct service and storage area in the AgentMem package; it does not reuse or merge the knowledge-base evidence corpus. Register each repository or Git worktree explicitly from the MCP with `repository_register(project_id, repository_root)`. Save the returned `repository_id`, then use `repository_list`, `repository_status`, `repository_search`, and `repository_resync`. Search results cite repository-relative paths and line ranges and report the indexed hash, whether the current file still matches, the index revision, and watcher freshness. Use KB tools for authored requirements, decisions, and work records; use repository tools for current code; use both to compare code with recorded intent.

The MCP process starts a bounded filesystem-event watcher for registered roots. It batches saved-file changes briefly, indexes changed files (including edits whose size and modification time are preserved), removes deleted or newly excluded files, and reconciles the whole tree on startup. Periodic metadata reconciliation recovers missed events when file metadata changed; if a filesystem event is lost for an edit that preserves size and modification time, call `repository_resync`. A per-worktree OS lock elects one watcher across multiple MCP clients; a separate writer lock serializes explicit resync requests with the watcher. Searches read the last committed SQLite FTS snapshot. If the watcher is stopped or misses a change, status reports pending events when observed; call `repository_resync` before relying on results. The watcher runs only while an AgentMem MCP process is alive; it is not an installed or machine-wide daemon.

`AGENTMEM_DATA_DIR` selects the locator registry and migration-backup location. `AGENTMEM_MEMORY_ROOT` selects the default parent for per-project KBs; it uses its platform default when unset. `--data-dir` and `--memory-root` are also available in the CLI. To inspect the registered command, run `codex mcp get agentmem`.

## Storage layout and migration

The per-project `agentmem.sqlite3` is authoritative for that project's tasks, dependencies, claims/checkpoints, receipts, lessons, requirements, decisions, proposals, approvals and histories, source hashes, and FTS evidence index. `PROJECT.md` is a compact brief and navigation page. Detailed narrative lives in `knowledge/` (purpose, architecture, risks, history, and materials by default). `requirements.md`, `decisions.md`, and `approvals.md` are generated views of SQLite records; do not create editable duplicates in topic pages. A project manifest identifies the knowledge base and source root. The app registry contains only project ID, source root, description, and KB path so it can route requests. Source files stay in their original repositories; the index stores cited text excerpts and hashes, not copies of those repositories.

New registrations create five empty topic pages with “Not documented yet” placeholders and a linked brief; they do not invent project facts. For a previously registered project without a `knowledge` directory, run `knowledge init <project_id>`. It only creates missing pages and preserves the existing brief and pages. Review and add brief navigation separately with `update_project_brief` if needed.

Before opening a per-project database, the server checks the knowledge-directory manifest and source-root binding. It rejects missing stores, mismatched project identity, and knowledge directories/databases replaced by symlinks or junctions.

On first startup after the upgrade, AgentMem makes a WAL-consistent backup of the former shared database under `.agentmem-data\migration-backups`, copies and verifies each registered project's records into separate stores, then switches the locator registry. It retains the old database as a recovery copy with write guards on its relational tables. Those guards do not cover SQLite's FTS virtual table or direct file writes from an older server, so shut down pre-migration MCP processes. After migration, restart Codex or open a new session to load the current server and tool definitions. For a custom KB location, set `AGENTMEM_MEMORY_ROOT` consistently for both MCP and CLI processes.

## Use it in Codex

Try prompts like:

- “Attach to the `agentmem` project and explain what this module does. Cite the project files you used, and tell me what is not indexed or known.”
- “Attach to `agentmem`, show the topic-page inventory, then read the architecture and risks pages. Explain how project facts are stored and cite the relevant lines.”
- “For project `example-project`, what are the current API constraints? Attach the project with this question and answer from its cited evidence.”
- “For project `example-project`, add a task to verify the controller change. Set acceptance criteria, authorized paths, and relevant dependencies. Do not start it yet.”
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
requirements(action="create", project_id="example-project", title="Keep public API stable",
  statement="Do not remove existing public methods without explicit approval.",
  original_quote="Keep the existing public API compatible.",
  interpretation="A breaking removal needs approval; additions are allowed.",
  lifecycle="current", fulfillment="unknown",
  scope={"project_wide":true}, source_type="user_conversation",
  source_ref="current Codex conversation", idempotency_key="capture-api-rule-01")

tasks(action="create", project_id="example-project", title="Refactor controller",
  goal="Improve controller internals without breaking the public API.",
  components=["controller"], requirement_ids=["REQ-0001"],
  acceptance=["tests pass", "existing API remains compatible"])

claim_task(project_id="example-project", task_id="...", owner="codex-main")
task_context(project_id="example-project", task_id="...")
```

When recording completion, include the `requirements_revision` returned by `task_context`, and a `requirement_checks` entry for each applicable requirement with its ID, revision, `met=true`, and evidence. This check means the agent reviewed and complied with the requirement in this task; it does not claim the requirement is globally fulfilled. The usual task acceptance checks still apply. The receipt does not automatically mark a requirement fulfilled; record that explicitly with `requirements(action="fulfill", expected_revision=..., fulfillment="fulfilled", fulfillment_evidence=[...], idempotency_key=...)` only when the evidence warrants it. Create a replacement with `supersedes_id` before marking the previous requirement superseded. Use `requirements(action="history")` or `decisions(action="history")` to inspect prior revisions. If a generated `requirements.md` or `decisions.md` was manually edited, `sync` reports a conflict; use `sync(force=true)` only after deciding to preserve the file's edits in the automatic backup.

Structured records and revision histories live in that project's SQLite DB. The readable `requirements.md`, `decisions.md`, and `approvals.md` files are generated views; use the MCP/CLI to change their records. SQLite updates use stable IDs, expected revisions and idempotency keys to surface conflicting writes. Database schema v10 adds structured lesson applicability metadata and versioned task acceptance history. Export schema v6 includes record histories and full approval snapshots; Markdown contents and search chunks remain in the per-project KB or are rebuilt from registered sources. Restart Codex to load updated MCP tools.

Task acceptance criteria are versioned and retained in task history. A claimed task can revise its criteria only through `tasks(action="revise-acceptance")`, with the current lease, an idempotency key, a change note, and source provenance. Retrieve prior criteria with `tasks(action="history")`. Completion receipts must match every criterion in the saved acceptance revision; after revision, include that `acceptance_revision` in the receipt. Partial, duplicate, or unrelated checks cannot satisfy completion.

Export structured records with `agentmem export <project-id>` and restore them with `agentmem import <project-id> <export.json>`. Import is limited to an empty ledger whose project ID and registered source root match the export. Task leases are cleared. The JSON contains a path/hash manifest rather than Markdown bodies or source contents; copy the project KB files with it when a full file backup is needed. After import, run `agentmem index <project-id>` to rebuild current evidence.

### Explicit approval flow

An approval is a recorded decision on a specific proposal revision and the exact requirement/decision revisions it references. Proposals are drafts until an explicit `approve` or `reject` action records the supplied user wording and provenance. The service cannot see the conversation or independently authenticate the person named in `recorded_by`; the calling agent must quote the actual approval and identify its source while it is available. Never infer approval from a task being done, an existing requirement being current, or silence.

Proposal revisions and approval events are retained. If proposal text or a pinned target changes, the previous event remains in history but the current proposal reports a stale status; it does not inherit approval. Even a fulfillment-only target revision requires a new proposal pin and explicit review under the current exact-revision policy. Approval is not fulfillment and does not mark a task complete. Gating is opt-in: create a task with `approval_proposal_id` only when that work must wait for approval. Ordinary requirements and tasks remain usable without retroactive approvals.

Example MCP flow:

```text
proposals(action="create", project_id="example-project", title="Controller rollout",
  content="Replace the internals while preserving the public API.",
  targets=[{"kind":"requirement","id":"REQ-0001","revision":2}],
  approval_required=true, created_by="codex-main", idempotency_key="rollout-draft-1")
proposals(action="show", project_id="example-project", proposal_id="PRP-0001")
proposals(action="approve", project_id="example-project", proposal_id="PRP-0001",
  expected_revision=1, original_quote="Yes, approve this exact rollout.",
  source_type="user_conversation", source_ref="current Codex conversation",
  recorded_by="user-supplied wording", idempotency_key="rollout-approved-1")
tasks(action="create", project_id="example-project", title="Implement controller rollout",
  approval_proposal_id="PRP-0001", acceptance=["tests pass"])
```

Use `proposals(action="status"|"history"|"approval-history")` before work when the exact scope matters. `task_context` reports the linked proposal's current status. An `approval_required` proposal blocks claiming its task unless the exact current proposal and target versions are approved; if they become stale before completion, a done receipt is not accepted. The server records evidence supplied by its caller; it does not observe user chat, establish identity, enforce Codex behavior outside these task APIs, or require approval for unrelated changes.

## CLI

```powershell
agentmem project list
agentmem project resolve --cwd '<path-to-example-project-checkout>'
agentmem index example-project
agentmem check example-project
agentmem search example-project 'controller API constraints'
agentmem task list example-project
agentmem export example-project > example-project-agentmem-export.json
agentmem requirement create example-project --title 'Keep API stable' --statement 'Do not remove public methods without approval.' --quote 'Keep the public API compatible.' --scope-json '{"project_wide":true}' --source-type user_conversation --source-ref 'current Codex conversation' --idempotency-key capture-api-01
agentmem requirement list example-project --lifecycle current
agentmem requirement history example-project --id REQ-0001
agentmem task-context example-project --id '<TASK_ID>'
agentmem decision list example-project
agentmem decision history example-project --id DEC-0001
agentmem proposal create example-project --title 'Controller rollout' --content 'Preserve the public API while replacing internals.' --targets-json '[{"kind":"requirement","id":"REQ-0001","revision":2}]' --approval-required --created-by codex-main --idempotency-key rollout-draft-1
agentmem proposal show example-project --id PRP-0001
agentmem proposal approve example-project --id PRP-0001 --expected-revision 1 --quote 'Yes, approve this exact rollout.' --source-type user_conversation --source-ref 'current Codex conversation' --recorded-by 'user-supplied wording' --idempotency-key rollout-approved-1
agentmem proposal status example-project --id PRP-0001
agentmem proposal approval-history example-project --id PRP-0001
agentmem knowledge init example-project
agentmem knowledge list example-project
agentmem knowledge read example-project --id architecture
agentmem knowledge history example-project --id architecture
```

To add a page, supply a lowercase slug, title, and body text: `knowledge create example-project --id deployment --title 'Deployment' --content 'Document the verified deployment path.'`. To revise one, read it first and pass its current hash: `knowledge update example-project --id deployment --expected-sha256 '<SHA256_FROM_READ>' --content "# Deployment`n`nUpdated verified deployment notes."`. A revision conflict means reread the page and reconcile the change before trying again. Use a normal single-line slug; file paths are not accepted by the page API.

## Safety and limits

- Every search/task/lesson operation requires an explicit project ID. Working-directory resolution uses the most-specific registered root and never a shared mutable “current project.” Task `authorized_paths` are validated as project-relative paths without traversal; they guide the agent but do not replace Codex's own permission checks.
- The KB index and repository index are separate. Repository indexing is limited to readable text/code suffixes, bounded to 512 KB per file, excludes `.git`, common generated/secret paths, `.gitignore` matches, symlinks/junctions, oversized/binary files, and credential-shaped assignments. A scan cap or filesystem error is reported as incomplete and does not delete unseen indexed entries.
- Repository watching polls file metadata and hashes only changed files. Changes that preserve both size and modification time may not be noticed by polling until explicit `repository_resync`, which hashes every safe file; searches also verify current hashes for returned hits. The default 20,000-file scan cap is reported as incomplete rather than deleting unseen entries.
- FTS retrieval is lexical, not semantic. Search may miss paraphrases or material in unsupported/binary formats. A missing result is a gap, not evidence that a fact does not exist.
- Tool results are project data and remain below system/developer/user instructions. The MCP does not change the model's identity or guarantee the model calls tools.
- Lease tokens are local coordination secrets; do not paste them into project Markdown. The local database is not encrypted by this module. Protect JSON exports and the Windows account/storage.
- Setup selects an available Python interpreter at version 3.11 or newer, matching `pyproject.toml`; pass `-PythonExe <path-to-python.exe>` to choose one explicitly. The MCP server remains a local stdio process; its watcher is tied to that process lifetime and has no always-on service manager integration.

The current implementation crosswalk, tested behaviors, and exclusions are recorded in [INFORMATION-POLICY-IMPLEMENTATION.md](INFORMATION-POLICY-IMPLEMENTATION.md). It is a status report, not a second policy authority; the full policy remains in the AgentMem project knowledge base.

## Development checks

```powershell
Set-Location <path-to-agentmem-checkout>
python -m pytest -q
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for boundaries, records, MCP operations, and extension points.
