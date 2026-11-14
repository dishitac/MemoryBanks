# MemoryBank: 90-second demo and interface handoff

This is the agreed presentation outline and spoken script for Person 2 to use when building
the interface. It is not a runnable terminal script. The governance backend exists; the
tailored USD 0 fixture, terminal presentation, and interface wiring still need implementation.
Do not display the expected outcomes below as if they were returned by a live run.

## The story

One person uses two simulated agents that share governed memory. Muse suggests a change.
Instinct can act on that change only after the person explicitly approves it. Forgetting
the memory removes its authority for future actions.

Use three clearly labeled identities throughout:

| Display name | Responsibility | Existing server binding |
| --- | --- | --- |
| Shopper | Requests headphones; explicitly reviews the proposed limit | Purchase: `employee` + `agent_a`; review: `finance_admin` + `agent_b` |
| Instinct | Retrieves approved memory and proposes the purchasing action | Agent A, `agent_a` |
| Muse | Interprets the proposed memory change; cannot approve it | Agent B, `agent_b`, initiated by `employee` |

These names are presentation labels, not connections to real assistant products. The Shopper
appears in two explicitly selected demo sessions: employee for purchases, authorized reviewer
for approval and forgetting. Show the session switch. This is simulated role switching, not
production authentication and not an employee gaining administrator rights through chat.
Tenant and team remain `demo_company` / `procurement` for the main story. The server binds
all identities and source kinds; user text and model output cannot change them.

## Prepare before the timer starts

1. Create a uniquely named disposable Atlas database. Never reset existing application history
   or change the configured application database to obtain these outcomes.
2. Set up validators and indexes, seed the demo principals, and prepare a fresh Northstar policy
   with `supplier_id=northstar`, `currency=USD`, `max_without_approval=0`, version 1, and a future
   expiry. The ordinary seed currently starts at USD 5,000: it is not this tailored fixture.
   Prepare the fixture at initialization, before embedding, rather than editing an activated
   policy behind the gateway. Person 1 owns this fixture preparation.
3. Embed the actual policy with Voyage, create the Vector Search index, and wait until v1 is
   retrievable through the gateway. Keep canonical supplier-policy text in storage; “Ask me
   before buying” is the short display explanation of the actual USD 0 terms.
4. Start the purchasing service with a separate disposable SQLite database. Start one demo
   application process. The service persists mock approval requests and draft orders only.
5. Bind the supplier to Northstar in server-owned scenario context. Display “Headphones from
   Northstar — USD 180.” The current action schema does not store a headphones SKU or cart;
   headphones are the narrative, while supplier, amount, and currency are the checked fields.
6. Open Atlas to this disposable database's `memories` and `runs` collections. Keep the
   database alive through the Atlas inspection; drop only this database when the demo ends.
7. Clear previous visible demo results and use fresh agent conversations. Check provider
   availability without creating an extra order. Do not print credentials.

Target 90 seconds of presentation after preparation. Gemini latency and v2 indexing are live
dependencies: show waiting and allow more time if necessary. Never substitute prepared
success text for an unverified result.

## Presentation script

### 0–10 seconds: opening

Say:

> “MemoryBank lets agents share memory, while the person controls which changes become actionable. These are simulated agents and mock purchases.”

Show the three names and the active employee session.

### 10–30 seconds: Instinct asks before buying

Read and display the approved memory through the gateway:

```text
Approved memory v1: Ask me before buying.
Automatic purchase limit: USD 0
Supplier: Northstar
Memory ID: <actual v1 ID>
```

Shopper requests exactly:

> “Get me these headphones for USD 180.”

Send this message to Agent A with the server-bound Northstar supplier context. On the actual
verified approval-request result, display:

```text
Instinct → Approval required
Mock approval request saved and verified
Supporting memory: v1 — <actual v1 ID>
Purchasing record: <actual approval-request ID>
Run: <actual run ID> — verified
```

Say: “The approved limit is zero, so Instinct asks first.”

### 30–45 seconds: Muse proposes a change

Ask Muse to suggest automatic Northstar purchases up to USD 200. Supply the current policy
key, expected version 1, and terms from server context. Bind the source to `employee_message`.
Do not let Muse claim finance authority or invoke review.

After the gateway persists the proposal, show:

```text
Muse → Suggest automatic purchases up to USD 200
Proposal: <actual proposal ID>
Proposal status: PENDING REVIEW
Active memory: v1 — USD 0
```

Repeat the identical Shopper request through Instinct. Read back the new mock approval request
and show its actual record/run IDs:

```text
Instinct → Approval required
Supporting memory: v1 — <actual v1 ID>
Pending proposals cannot authorize purchases
```

Say: “A suggestion is stored, but it has no authority yet.”

### 45–65 seconds: the Shopper explicitly approves

Switch visibly to the authorized Shopper reviewer session. Show the stored proposal's
submitted text, effective instruction, supplier, currency, USD 200 limit, and expected v1.
Require a separate human command or click; sending a chat message is not approval.

Example interaction for the future terminal runner, not an existing CLI command:

```text
Shopper [authorized reviewer]
Approve proposal <actual proposal ID>, replacing v1? [approve / cancel]
> approve
```

Call `approve_proposal` with the exact proposal ID and expected version 1. Only after success:

```text
Shopper → Approved the USD 200 limit
Memory v1 → Superseded — <actual v1 ID>
Memory v2 → Active — <actual v2 ID>
```

Wait until scoped search returns v2. Display “Waiting for approved memory to become searchable”
while necessary. Do not automatically repeat a purchase until it creates an order.

Switch back to the Shopper purchase session and repeat the identical request once:

```text
Instinct → Mock draft order saved and verified
Amount: USD 180
Supporting memory: v2 — <actual v2 ID>
Purchasing record: <actual draft-order ID>
Run: <actual run ID> — verified
```

Say: “Same request. The person approved the memory, so the permitted action changed.”

### 65–85 seconds: evidence, isolation, and forgetting

Briefly show Atlas before forgetting:

- The actual v1 and v2 records, their statuses, terms, and supersession links.
- The verified draft-order run referencing v2 and the purchasing record ID.

Return to the terminal. Attempt both scoped retrieval and direct-ID access using the other-team
session. The existing marketing user paired with a procurement agent is denied by identity
checks; do not claim this demonstrates an independently authorized marketing agent.

```text
Other scope → Access denied / no procurement memory returned
```

Switch to the authorized reviewer and explicitly forget both v1 and v2 through the gateway,
using their actual IDs and expected versions. Forgetting only v2 blocks future actions but
leaves superseded v1's content stored. Show completion only when both operations succeed:

```text
Policy forgotten → Content and embedding removed from v1 and v2
Linked controlled records → Redacted metadata retained
```

Clear previously displayed policy/proposal content from the interface. Use a fresh Instinct
conversation and repeat the same purchase request. Confirm the purchasing-record count has
not increased:

```text
Instinct → Needs clarification; no new order created
Run: <actual final run ID> — clarification
New purchasing records from this attempt: 0
```

Existing external approval requests and draft orders remain. Forgetting does not undo them
or delete provider logs/backups. The claim covers these versions and controlled linked copies.

### 85–90 seconds: closing

Say:

> “The request stayed the same. The action changed only after the shopper approved the memory—and forgetting that memory removed its authority.”

## Person 2: interface build contract

The terminal is the main presentation. A minimal web interface can use the same sequence and
labels. Do not add a framework, Streamlit, new dependency, or external assistant/store connection.

1. Provide explicit session controls and separate Instinct purchase, Muse proposal, and human
   review controls. Shopper approval/forgetting bind to the existing authorized reviewer.
2. Reuse the connected agent/gateway/dispatch functions. Purchase execution must use the final
   guarded dispatch and HTTP read-back; `validate_action` alone is not verified execution.
3. Supply current policy context to proposal interpretation and enforce the returned key/version
   against that context in server code. The gateway still makes the final decision.
4. Keep review and lifecycle outside model tools. Require an explicit approval command/click
   and exact expected version. A pending proposal never becomes Agent A's actionable memory.
5. Render actual persisted proposal status and purchase outcome, memory ID/version, run ID,
   and purchasing-record ID. A blocked/clarification result has no purchasing-record ID.
   Preserve `failed` and `uncertain`; never render either as success.
6. Track IDs returned during this run for review, Atlas inspection, direct-ID checks, and
   forgetting. Scope all evidence reads to the session. Show no procurement payload to another
   team. After forgetting, refresh records and remove stale content from the displayed view.
7. Expose preparing/index-wait states and short provider, permission, and version-conflict
   messages. Keep controls usable after an error. No secrets or raw credential-bearing errors.
8. Keep the disposable runtime injectable so local interface tests use fakes and cannot
   accidentally provision Atlas or call Gemini/Voyage.

Person 1 owns the tailored fixture and any required backend governance changes. Person 2 owns
interface/session wiring and the small agent/dispatch integration changes already agreed.
Do not modify schema, seed, gateway, proposals, actions, lifecycle, or the acceptance test as
part of interface work without coordinating with Person 1.

The 90-second story is a presentation subset. Preserve all nine checks in the README's
“Final interface checklist for both teammates,” including stale/unrelated citations,
expiry/revocation, denied employee review, full evidence, and service-level idempotency.
The USD 5,000 / USD 10,000 acceptance fixture stays unchanged; test the USD 0 / USD 200
presentation fixture separately. Mark interface completion only after live checks pass.
