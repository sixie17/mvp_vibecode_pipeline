# Ideas

Speculative, unresolved directions this pipeline might grow into — not committed architecture. Contrast with [CLAUDE.md](CLAUDE.md), which documents what's actually decided (even if not yet built) and its "Open design decisions" section, which tracks decisions that are blocking and need an answer before the relevant lane can proceed. An idea here graduates to CLAUDE.md once it's actually decided; until then it stays here so it doesn't get mistaken for settled design.

---

## Lane 1: codebase context and execution environment (for steps 5–8)

Steps 5 (plan) and 6 (code) can't act on an arbitrary target repo without some way to understand it, and step 7 (tests) needs to actually run that repo's test suite.

**Decided and built for step 5**: no persistent index — `planner/` clones the target repo into a temp dir per call and gives a tool-using agent live `grep`/`read`/`list_files` tools scoped to that clone, the same operating model Claude Code itself uses. See CLAUDE.md's `planner/` entry. Steps 6–8 don't reuse this yet but plausibly could once they exist. The reasoning below is why this shape was chosen; still open is everything about steps 6–8's own execution needs.

**If an index ever becomes necessary** (a target repo too large or slow for live search to hold up): vector DB (embeddings over chunked code) and graph DB (files/functions/classes/calls as nodes/edges, built from tree-sitter/LSP output) answer different questions — vector is good for fuzzy/semantic search, graph is more precise for structural queries ("who calls this") but harder to build generically across arbitrary languages. Lean vector-first if this is ever needed; add graph-based lookups later only for queries semantic search can't answer well.

**If an index exists, it should be a store the user configures, not one this app hosts** — same pattern already used for Linear/GitHub/the LLM provider (an env var pointing at a connection string/endpoint, e.g. Pinecone/Qdrant/pgvector). This doesn't violate the stateless principle even in spirit: that rule protects against a second, possibly-stale system of record for *ticket pipeline state* (where Linear/GitHub are the sole source of truth). A code index is a different category — a rebuildable cache of the target repo's own content, with the repo itself remaining the source of truth. Never let the index's presence/absence or content be a signal this app reasons about ticket/PR state from.

**Does the target repo need to be cloned?** Yes, in practice. GitHub's Contents/Git Trees API can read file contents and directory structure without a clone, and its code search API does keyword lookups — so read-only exploration could theoretically avoid cloning. But there's no way around it for step 7: GitHub's API has no execution capability (Actions is async CI, not something an agent loop can invoke mid-reasoning and get results from conveniently), so actually running a test suite requires a real filesystem with the target stack's toolchain. Since a clone is required anyway for execution, it's simpler for step 5's exploration to use that same real clone with real `grep`/`read` tools rather than maintaining two different access patterns (API reads for browsing, a clone for execution).

**Where does the clone live?** Scratch space for the duration of one call — `planner/workspace.py`'s `cloned_repo()` context manager clones into a fresh temp directory and always removes it on exit, success or failure. Today that call happens inline wherever `plan_change()` is invoked; once an async task queue exists (see "Long-running work" in CLAUDE.md), the same context manager just runs inside whichever worker process picks up the job — nothing about the clone/cleanup logic itself needs to change. This is ephemeral execution state, not persisted business state — the same distinction that makes a CI runner cloning a repo into a throwaway container "stateless" in the sense that matters. The one invariant to hold: never let "do we already have a clone for this ticket" become a signal this app reasons from — pipeline-state decisions still always come from asking Linear/GitHub, never from local disk.

**Open sub-question, not yet addressed**: since target repos are arbitrary-stack, the execution environment itself needs to detect and install that repo's toolchain before tests can run. Possibly by reading the repo's own signals (a `Dockerfile`, `devcontainer.json`, CI config) rather than this app hardcoding assumptions per language — unexplored.

**Resolved, moved to CLAUDE.md**: *which* GitHub repo to clone for a given Linear issue turned out not to be derivable from Linear's API at all — confirmed against their public GraphQL schema, no field on `Issue`/`Team`/`Project` exposes it. Settled as config (`TARGET_REPO_CLONE_URL`/`TARGET_REPO_DEFAULT_BRANCH`, one repo per deployment) rather than something read at request time — see CLAUDE.md's "Prerequisite" section. Still open, and still here rather than there: whether a per-team/per-issue mapping ever becomes necessary (tracked in CLAUDE.md's "Open design decisions" too, since it's a real fork, not pure speculation).

---

## Lane 2: detection strategy

Don't run an LLM over the full raw stream of logs/telemetry — that burns tokens on the overwhelming majority of lines that are routine. Instead, a cheap non-LLM first pass (regex/rule-based error matching, or a statistical anomaly detector) filters the stream down to what's actually concerning, and only that reduced subset goes to an LLM step that decides whether to file a ticket and drafts it.

Open sub-questions, not yet resolved:
- What the cheap filter actually is — hand-written rules vs. a real anomaly-detection model vs. wrapping existing log-analysis tooling.
- Where it runs relative to this app — inside this repo, or a separate lightweight service Lane 2 calls.

---

## Parking lot

Ideas noted in passing that haven't been explored yet:

- (none yet)
