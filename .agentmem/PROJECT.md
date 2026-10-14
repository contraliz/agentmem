# Agent Memory local MCP

## Purpose and boundary
A reusable project-scoped context and work-tracking module for Codex, intended to be one component of a larger project-work system. The service returns project knowledge, citations, task state, checkpoints, and accepted lessons to the agent that calls it. It does not choose model roles, instruct delegation, launch another agent, execute project work, or schedule jobs. Task goals, authorized paths, criteria, checkpoints, and receipts are declarative project data; Codex instructions/runtime govern how the caller uses them.

## Verified implementation
- Python domain/storage layer shared by the MCP server and CLI.
- SQLite stores project task state, dependencies, claims/checkpoints, receipts, lessons, and a rebuildable FTS5 source index. Curated project context is Markdown in this folder.
- Codex MCP entry `agentmem` is configured as a stdio server; data lives at `[local path redacted]\.agentmem-data\agentmem.sqlite3`.
- `attach_project(project_id, query=...)` combines orientation and up to three focused, cited excerpts in one tool call. `include_sources=true` opts into the full path/hash manifest; the default packet includes source count/revision and citations retain hashes. Deeper searches remain available through `retrieve_evidence`.
- `*.egg-info` build metadata is excluded from indexing and change scans.
- Task claims use ownership leases; task context includes checkpoints, matching evidence, and accepted lessons. Receipts require evidence for every acceptance criterion before completion.
- Current test suite: 19 passed, 1 skipped (2026-10-14). The remaining skip is regular-file symlink creation unavailable to this Windows test account; the Windows junction-escape test passes.

## Measured local performance
Warm in-process benchmark, 50 samples per operation, on this 16-source pilot, 2026-10-14. Values are local service/store timings and compact JSON character counts; no live model response was measured.
- Baseline commit `a7eda6a`: attach p50 16.774 ms / p95 18.383 ms; separate 5-result search p50 2.788 ms / p95 2.945 ms; combined two-call output 18,233 characters.
- Current: attach p50 13.620 ms / p95 15.902 ms; bundled attach plus query p50 16.319 ms / p95 17.375 ms; output 8,396 characters.
- The common question path is one tool call instead of two, with about 54% fewer returned characters. The bundled path caps excerpts at 1,200 characters and returns up to three results; use deeper retrieval when evidence is incomplete or ambiguous. End-to-end latency still depends on model reasoning and Codex orchestration.

## Freshness and limits
Indexing is manual. Attach performs a path/mtime/size scan but does not read file contents. Run `index_project` after edits to refresh hashes and line citations. Retrieval is lexical FTS5 plus a CJK substring fallback, not semantic search. Unsupported/binary files and excluded secret/generated paths are absent. Context and cited evidence are inspectable, but no arbitrary question is guaranteed to have an indexed answer.

## Codex load state
The global Codex MCP config includes this server. A fresh Codex session may be needed to load updated tool parameters. Independent MCP SDK stdio tests pass; no live model/answer latency has been measured.