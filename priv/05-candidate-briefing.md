# Candidate Briefing - How to Present This Project

> Everything you need to walk into the conversation confident: the pitch, the
> reasoning behind every choice, what to say, what to volunteer, and what to avoid.
> Read `03-interview-qna.md` for the full question bank; this file is the strategy
> around it.

---

## 1. The one-minute pitch (memorise the shape, not the words)

"The task is: when the `orders` table changes, connected clients should see it
instantly, without polling. The key decision is *where* you detect the change. If
you push updates from your API code, you only catch changes that go through your
API, and you miss anything done by a background job, another service, or an admin
running SQL. So instead, a trigger inside Postgres fires on every insert, update,
and delete and sends a notification. The backend listens for those and pushes each
change to all connected clients over Server-Sent Events. When a client connects, it
first gets a full snapshot of current state, then the live stream, so a client that
just joined or just refreshed is always correct. I proved the core claim by changing
a row directly in the database, bypassing the API, and every client still updated."

That is the whole thing. If you can say that clearly, you have already done well.

---

## 2. Why each decision, in one line each

- **Change detection in the database (trigger + LISTEN/NOTIFY), not in the API:**
  it is the only way to catch *any* writer, and the notification is tied to the
  transaction commit, so clients never see a change that rolls back.
- **Server-Sent Events, not WebSockets:** data flows one way (server to client), and
  SSE does exactly that over plain HTTP with automatic reconnection. WebSockets add
  a two-way channel we would never use.
- **Snapshot first, then live changes, on one stream:** a client needs current state
  on connect, not just future changes. Subscribing before reading the snapshot
  closes the gap so nothing is missed during a refresh or a late join.
- **De-duplicate by `updated_at` on the client:** makes duplicates and out-of-order
  messages harmless, so the snapshot-plus-stream approach is safe.
- **In-memory fan-out (the hub):** simplest thing that works; replicas still each get
  every change from Postgres, so no shared state is needed at this scale.
- **Drop slow clients (bounded queue):** one stuck client can never slow everyone
  else down; it reconnects and re-syncs on its own.
- **pg_notify, not Redis pub/sub:** transactional, catches every writer, no extra
  infrastructure; Redis is a scaling step for later, not a correctness upgrade (see
  `03-interview-qna.md` Q20b, this is the most likely deep-dive).
- **Static HTML client, not a framework:** the real-time pipeline is the point, not
  UI tooling; a CLI client is included to show the stream is not tied to one UI.
- **Docker Compose:** one command runs Postgres and the backend together, so it is
  trivial for someone else to run.

---

## 3. What to volunteer before they ask

Bringing up your own limitations makes you look thorough, not weak. Early in the
conversation, say something like:

"A few things I deliberately left out to match the scope: authentication, an
automated test suite in the repo, and event *replay*. On replay: a client that is
offline during a change misses that specific event, but because it re-syncs to a
full snapshot on reconnect, it is always correct afterward, it just cannot show the
individual events it missed. If I needed a gap-free history I would add an outbox
table or a durable log."

This pre-empts their hardest questions and frames the gaps as choices.

---

## 4. What to say vs. what to avoid

**Do say:**
- "For this scope" and "the source of truth stays Postgres." These show you are
  matching the solution to the problem.
- "It scales horizontally by adding replicas, and here is the one shared ceiling
  (the Postgres write rate) that adding replicas does not raise." Precise and honest.
- "I verified this" when you talk about the direct-database test and the late joiner.
  You actually ran it; say so.

**Avoid:**
- Calling it "production ready." Say "production-minded design; not fully hardened
  (no auth, tests, observability, TLS yet)." Claiming more invites them to find the
  holes.
- Quoting exact throughput or latency percentiles. You have not benchmarked, so do
  not invent numbers. Talk about the *shape* of the limits instead (see next point).
- Over-claiming about Redis. Do not say "Redis would be better." Say "Redis is the
  right next step when fan-out outgrows one process, but it would not have solved a
  problem I have at this scope."

---

## 5. Talking about scale without numbers

If they ask "how much can it handle," you do not need percentiles. Explain the three
limits in plain language:

1. **How many connections one process holds open** - cheap while idle, bounded by
   memory and OS file limits.
2. **How much fan-out it can do** - roughly the number of clients times the change
   rate, because every change is written to every client. This is usually the first
   thing to give.
3. **The write side** - every change goes through one Postgres `NOTIFY`, and adding
   backend instances does not raise that shared ceiling.

Then: "You grow the first two by adding replicas. When you approach the third, or
when fan-out outgrows a single box, that is when a Redis fan-out layer earns its
place." That answer sounds more senior than any single number would.

---

## 6. If you get stuck or they find something you did not think of

Say: "Good point, I had not considered that. Here is how I would reason about it..."
and think out loud. They are testing how you think, not whether the project is
perfect. Composure and honest reasoning score higher than pretending.

---

## 7. Quick reference: where each answer lives

- Full question bank with answers: `03-interview-qna.md`
- Why each choice, with costs: `../docs/design-decisions.md`
- The cases the design already handles: `../docs/production-notes.md`
- How data flows and the connect flow: `../docs/architecture.md`
- Every technical term defined: `02-concepts-explained.md`
- The whole system in plain language: `01-project-explained.md`
