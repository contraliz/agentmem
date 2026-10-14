# Agent Memory local MCP

## Purpose
A reusable project-scoped memory module for a larger project-work system. This first implementation provides a local Codex stdio MCP and CLI; it is not the larger manager runtime or scheduler.

## Verified implementation
- Python domain/storage layer shared by MCP and CLI.
- SQLite stores project task state, dependencies, claims/checkpoints, receipts, lessons, and a rebuildable FTS5 source index. Curated project context is Markdown in this folder.
- Codex MCP entry `agentmem` is enabled as a stdio server; data lives at `[local path redacted]\.agentmem-data\agentmem.sqlite3`.
- The CLI wrapper works outside this workspace. The project has passed unit and real stdio subprocess tests (17 passed, 1 skipped; 2026-10-13). The remaining skip is the regular-file symlink test because creating that symlink was unavailable to the Windows test account; the Windows junction escape test passed.
- Attach/search are evidence-only operations with file/line/hash citations; the model forms answers. Task claims use an ownership lease, context packet, checkpoint, and evidence-backed receipt.

## Freshness and limits
Indexing is manual; attach performs a path/mtime/size freshness scan but does not read contents. Run `index_project` after edits to verify hashes and refresh citations. Retrieval is lexical FTS5 plus a CJK substring fallback, not semantic search. Unsupported/binary files and excluded secret/generated paths are absent.

## Codex load state
The global Codex MCP config now includes this server. Open a fresh Codex session to load the new tools. A noninteractive live model smoke could not run because the current account rejected the selected model IDs; the independent MCP SDK stdio handshake and tool calls pass.
