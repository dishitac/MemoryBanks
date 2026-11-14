# MemoryBank: instructions for coding agents

Read [README.md](README.md) first. This file is named `agent.md` as requested; tools that discover only `AGENTS.md` must be explicitly directed to read it.

## Project contract

Build **MemoryBank**, a two-agent shared-memory governance prototype for **Memory You Can Govern**. Use the supplier-purchasing scenario, not ticket routing. Agent B proposes memory updates; Agent A retrieves approved memories and proposes purchases; server code controls trust and execution.

Checkpoint 1 implements collections, schema validation, standard indexes, non-destructive seeding,
and eight real Atlas tests plus a local search-fixture test. Five synthetic memories cover
multiple suppliers, an unrelated office-supplies policy, private marketing scope, and a retired
Northstar instruction. The retired fixture uses a separate policy key, not invented history
of the existing active v1. Checkpoint 2A adds `memorybank/embeddings.py` and
`python -m scripts.embed_memories`: real Voyage document/query helpers, response validation,
model/dimension/content-hash metadata, and guarded fixture updates. Nine local boundary tests
and two Atlas storage tests cover this work; the storage tests explicitly simulate API responses,
while real Voyage embedding preparation was verified separately. Checkpoint 2B implements
`memorybank/search.py`, index setup and search CLI scripts, and the read side of
`memorybank/memory_gateway.py`. Use `MemoryGateway(db, embedder).retrieve_memories(identity,
query_text, supplier_id=None)` with a server-created `ServerIdentity`; response records contain
only `memory_id`, `version`, `policy_key`, `content`, and `terms`. `get_memory` provides protected
direct-ID reads. Four local index tests, thirteen Atlas read-boundary tests, and three real
Voyage/Vector Search tests cover this checkpoint. Tests call Voyage only with `--atlas --search`.
Checkpoint 3 adds `MemoryWriter` in `memorybank/proposals.py`, gateway proposal/review methods,
and a shared single-process `policy_lock`. Valid employee suggestions remain pending; claiming
finance authority as an employee is rejected. Only an in-scope finance administrator on the
Agent B server path may explicitly review. `source_kind` uses the partner's existing enum;
the server, not the model, establishes its value. Active text is generated from reviewed typed
terms and embedded before an atomic correction. Proposals are immutable through the gateway;
payload fingerprints detect out-of-band edits. Approval preserves the expiry deadline.
Fourteen local and thirteen Atlas tests cover the workflow. The real-Voyage demonstration
`python -m scripts.demo_review` uses/removes only a disposable demo database.
The partner can integrate retrieval, proposal creation and explicit review now. Keep review
out of model tools. `validate_action` is not implemented, so retrieval must not authorize
purchases. Future dispatch/lifecycle code must reuse `policy_lock` to coordinate with review.
The purchasing service and Gemini interpretation helpers are implemented by the partner;
end-user orchestration and coordinated dispatch remain unfinished.
Keep status and runnable instructions accurate. Use the seed IDs and commands in
README. Do not reseed by deleting or resetting existing policies.

## Organizer requirements

Each project **must** be built in one of the following three problem statements. The requirements below are supplied by the user from the hackathon instructions.

### 1. Agents You Can Audit

An agent that can't explain what it did, and who allowed it, never leaves the sandbox. Build an agent using MongoDB Atlas as the identity, policy and audit layer (collections, schema validation, change streams) that takes real actions across systems, where every action is tied to an identity, checked against policy before it runs, and recorded so a reviewer can reconstruct it afterward.

- How do you decide what an agent may do and enforce it *before* the action, not after?
- How do you record what the agent did, why, and under whose authority?
- How do you stop or roll back an agent mid-run?

Examples:

- A claims-handling agent with per-step policy gates and a ledger a compliance reviewer can query.
- A KYC-refresh agent that is blocked from touching accounts outside its scope.

### 2. Memory You Can Govern — selected track

What an agent remembers is data someone has to answer for. Build an agent using MongoDB Atlas and Vector Search to persist and retrieve memory, so that memory changes what it does on the next run, with rules for who can read each memory, how long it lives, and how it gets corrected or forgotten.

- How do you keep one client's or team's memory from leaking into another's?
- How does the agent fix or drop a memory that turns out wrong or expired?
- How do you prove a memory changed the agent's behavior and didn't just fill the prompt?

Examples:

- An advisor-prep agent whose memory is scoped per client and per advisor.
- An incident-triage agent that remembers past fixes and lets them expire.

### 3. Finish the Job

Real work spans many systems and takes hours, and the systems won't hold still. Build an agent that completes a long multi-step task across several systems, using MongoDB Atlas to persist verified task state and Vector Search to retrieve relevant past experience, so it survives crashes and system changes and knows what actually happened.

- How does it tell an action that *took effect* from one that was only *sent*?
- How does it resume after a crash without replaying its whole history?
- How does it adapt when a system's layout, fields or workflow changes underneath it?

Examples:

- An address-change agent updates several systems, one refreshes mid-run, and the agent retries only the update that failed.
- A month-end reconciliation agent that resumes from checkpointed, verified state.

### Strictly prohibited anti-projects

- AI Mental Health Advisor
- Basic RAG Applications
- Streamlit Applications
- Image Analyzers
- “AI for Education” Chatbot
- AI Job Application Screener
- AI Nutrition Coach
- Personality Analyzers
- Any project using AI to generate and give medical advice
- Any project where a dashboard is the main feature
- Sports analyzers or coaches

## Build priorities and boundaries

1. Establish working Atlas and Vector Search early; do not substitute an in-memory list or keyword search and claim track completion.
2. Seed one organization, two teams for isolation checks, an employee, finance admin, and two agent identities.
3. Build gateway permissions and proposal separation before optional suspicious-content classification.
4. Complete baseline purchase, poisoning rejection, legitimate approved correction, and changed persisted action.
5. Implement scoped access, expiry, and forgetting for full selected-track coverage. Disclose any gaps in the two-hour core slice.
6. Keep CLI/web controls minimal. No Streamlit or dashboard-first development.
7. Add paused-action revocation, model-based risk assessment, and change streams only after the core works.

Stack: Python/FastAPI/PyMongo, Gemini through `google-genai`, Voyage embeddings, and SQLite for a separate mock purchasing service. Default model: `gemini-3.5-flash-lite`, configured through `LLM_MODEL`; key comes from `LLM_API_KEY`. Both agents share the client/provider but retain separate instructions, conversations, and tools. Handle free-tier rate limits with bounded retries and a clear user message; do not silently switch to a paid model. Keep dependencies small; no distributed workers or framework expansion without need.

## Non-negotiable governance rules

- Server-bound sessions establish user, agent, tenant/team, and role. Never trust these fields from prompts or model output.
- Preserve initiating user and submitting agent in tool context and events. A trusted agent cannot upgrade an unauthorized user's permissions.
- Demo identities must be seeded session choices, not arbitrary client-supplied roles; disclose that this is not production authentication.
- Agents have no Atlas credentials, arbitrary database tools, direct purchasing-service access, or self-approval tools.
- All reads/writes, including ID lookups, go through scope checks in the gateway.
- Keep `memory_proposals` separate from actionable `memories`; pending/rejected content is unavailable to A.
- All purchasing-policy changes require explicit finance-admin approval of the exact proposal revision. Edits invalidate prior review.
- Unauthorized is a policy finding, not proof of malicious intent. Suspicious or wrong claims need evidence and review.
- A model risk flag is advisory. It cannot establish permission, truth, or approval; assessment failure leaves protected changes pending.
- Supplier documents cannot authorize internal purchasing-policy changes. Record source references without pretending the reference verifies the claim.
- Validate approved text and typed terms agree; embed the approved revision. The model does not control executable policy through hidden inconsistent fields.
- Recheck Vector Search candidates against primary records for scope, lifecycle, and dates before exposing content.
- Revalidate supplier/type/currency/scope and current memory version immediately before dispatch. Similarity alone does not authorize action.
- Enforce numeric limits in server code, accepting known suppliers, USD, and positive finite amounts in the MVP.
- Missing, ambiguous, expired, revoked, superseded, deleted, or unrelated policy cannot authorize a draft order. Ask for clarification.
- Corrections use atomic transactions plus expected-version checks; prevent stale approval and multiple active versions.
- Serialize policy mutations and final dispatch per policy scope in one server. Do not claim independent distributed revocation.
- Use BSON dates and server-time validity checks. TTL is eventual cleanup, never authorization.
- Activated content is immutable except explicit forgetting; lifecycle/linkage transitions occur only through gateway operations.
- Audit events are server-authored, not inherently tamper-proof.

## Storage and execution rules

- Use README collections: `principals`, `memory_proposals`, `memories`, `memory_events`, `runs`.
- Add schema validation and version/active-policy uniqueness alongside application checks.
- Match Vector Search dimensions to the real embedding model and declare actual prefilter fields. Do not fabricate embeddings.
- Use synthetic data and mock purchases only. No real orders, payments, or supplier contact.
- Persist purchasing records outside the agent's conversation. Use idempotency keys and read back state before claiming success.
- Record memory IDs/versions, user/agent, proposed action, validation, receipt, and verified outcome. Preserve uncertainty when external completion is unknown.
- Prefer references over copied memory text in logs.
- Quarantine retains restricted evidence. Forgetting removes selected content/embedding and controlled copies in proposals/runs/events, keeping minimal non-content metadata.
- Do not promise deletion from backups, provider logs, completed actions, or arbitrary agent caches. Use fresh conversations on subsequent runs.
- Revoking a memory does not erase model context or undo completed actions. Reject pending actions supported by obsolete versions.
- Never log credentials. Ensure `.env` is ignored before storing secrets. Commit only placeholder configuration examples.

## Required checks

Use focused behavior tests for permission/lifecycle/action boundaries and real Atlas integration checks for retrieval.

- USD 7,000 under USD 5,000 policy creates a verified finance-approval request referencing v1.
- Employee using B cannot activate unlimited policy or impersonate an administrator.
- Supplier bypass instruction stays pending and unavailable to A as approved memory.
- Legitimate admin-approved USD 10,000 correction creates v2; same request creates a verified draft order.
- Cross-team search and direct-ID lookup both deny content.
- Expired or forgotten policy cannot support a new action; forgetting removes content, embedding, and controlled copies.
- Unrelated active memory fails applicability checks.
- Stale approvals cannot overwrite current policy; retries do not duplicate orders.
- If paused-action revocation is implemented, prove memory retrieved before revocation cannot authorize dispatch afterward.

Include legitimate corrections and unseen paraphrases, not only handpicked attacks. Do not infer general detection accuracy from scripted successes.

## Collaboration conventions

Explain Atlas and implementation steps in beginner-friendly language: the owner is new to MongoDB. Prioritize a working vertical slice and update README commands as code lands. Preserve user changes and do not mark planned features complete without evidence. Follow explicit user direction over project preferences, subject to higher-priority instructions.
