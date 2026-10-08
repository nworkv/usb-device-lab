---
name: usb-device-lab-commit
description: Prepare, test, review and push one bounded USB Device Lab change with explicit approval.
---

# USB Device Lab commit workflow

## Trigger

Use when the user asks to prepare the next commit, push a prepared change, or
continue the agreed USB modernization plan. This is a repository skill document;
it does not install a native Perplexity skill or authorize future writes.

## Defaults and context

- Repository: nworkv/usb-device-lab.
- Working branch: feature/kernel-triage; verify it on every write.
- Never change main, merge a PR, or switch targets without explicit instruction.
- Read the current plan and relevant code. Do not invent plan steps from this file.
- Existing order: executor stop, supervisor core, loopback HTTP panel, durable
  supervisor snapshot. PRNG checkpointing and event streaming remain separate.

## Fast, reproducible procedure

1. Discover GitHub tools and obtain schemas; record branch HEAD.
2. Read only the files needed for the selected step, using already retrieved
   content when current. Fetch missing implementation evidence, not just names.
3. Keep one functional scope per commit. Preserve existing CLI behavior.
4. Build the exact changed-file map once; reuse it for tests, diff, approval and
   push. Never hand-edit a second copy after approval.
5. Run targeted tests first, then the full repository suite when available.
   If testing an isolated class or mocked backend, say so; never describe it as
   a full repository or hardware test.
6. Review diff, imports, failure paths, cleanup, file locks, secrets and docs.
   Record which checks actually ran and their results.
7. Prepare owner, repo, branch, commit message and full content of every file.
   For external writes, request confirmation bound to these exact arguments.
   A request to work faster does not waive confirmation.
8. After approval, push exactly that payload in one commit. If target, contents
   or base conditions change materially, stop and request fresh approval.
9. Report returned commit SHA, changed files, checks and limitations. Do not
   announce a successful push before the connector returns success.

## Guardrails

- Shell/network commands are not run by the Python analysis environment; do not
  claim a local git clone, repository suite or physical UDC test without evidence.
- Never put lab credentials, bearer tokens or private logs in commits.
- Cooperative stop is not an emergency kernel/UDC reset.
- Resume with the existing corpus is not exact PRNG checkpoint continuation.
- Snapshot recovery must not automatically restart a hardware campaign.
- Keep complete drafts available for review, even if confirmation is verbose.
- Source tool schemas must come from discovery, not guessed names or arguments.
