# Trade-offs - Why Each Decision Was Made

> A trade-off means: every choice gives you something and costs you something.
> Good engineering isn't picking the "best" option in a vacuum - it's picking the
> option whose gains matter and whose costs don't, *for this specific problem*.
> This doc explains the reasoning behind each decision so you can defend it.
> Concepts in **bold** are defined in `02-concepts-explained.md`.

The problem this whole project solves has three constraints worth keeping in mind
as you read: (1) *any* change must be caught, not just ones through our code; (2)
no polling; (3) it's an interview task, so clarity and "easy to run and
understand" matter as much as raw capability.

---

## Decision 1 - Detect changes in the database (trigger + LISTEN/NOTIFY), not in the app

**The alternatives were:**
- **(A) App-level push** - after our API saves a change, our code also notifies
  clients.
- **(B) Database-level push** - a **trigger** fires on every change and the
  database broadcasts it. *(chosen)*

**Why B won:** Constraint (1) is decisive. Option A only catches changes that pass
through our API. A change made by another service, a scheduled job, or an admin
typing SQL would never trigger our notify code - clients would silently go stale.
The database is the *only* place that sees every write, so detecting changes there
is the only way to actually satisfy "any change." I verified this by updating a row
directly in the database and confirming clients still updated.

**What it costs:**
- A small amount of logic now lives *inside* the database (the trigger + a tiny
  PL/pgSQL function), not purely in application code. Some teams dislike logic in
  the DB. My counter: the trigger is thin and single-purpose (it only emits a
  notification, no business rules), and the guarantee it buys is exactly the
  requirement.
- The trigger runs on **every** write, adding a tiny bit of overhead per change.
  For this workload that's negligible.

**When I'd reconsider:** If notifications were only ever needed for changes that
*do* go through one service, app-level push would be simpler and fine.

---

## Decision 2 - SSE for delivery, not WebSockets

**The alternatives were:**
- **(A) WebSockets** - a full two-way real-time channel.
- **(B) SSE (Server-Sent Events)** - one-way server→client streaming over HTTP.
  *(chosen)*
- **(C) Long-polling** - a fallback older technique.

**Why B won:** The data here flows in exactly one direction. The server pushes
changes; clients only ever *receive*. **SSE** is purpose-built for that: it's plain
HTTP (so it works through most proxies and needs no protocol upgrade), and the
browser's `EventSource` gives **automatic reconnection** for free - something you'd
have to build yourself with WebSockets. Less code, fewer failure modes, and it maps
perfectly onto the shape of the problem.

**What it costs:**
- No client→server messaging on that channel. If clients ever need to *send*
  real-time data (typing indicators, chat), SSE alone won't do it.
- SSE has weaker support in very old browsers (not a concern for a modern demo).
- Browsers limit how many concurrent SSE connections you can open to one domain
  over HTTP/1.1 (~6). Fine for one stream per tab; over HTTP/2 this largely goes
  away.

**Why not WebSockets "just in case":** Adding a two-way channel you don't use is
extra complexity for no benefit - a classic case of choosing the simplest tool that
fits. It's easy to justify switching *later* if a real two-way need appears.

---

## Decision 3 - In-memory hub for fan-out, not an external message broker

**The alternatives were:**
- **(A) In-process hub** - a Python object holding the set of clients, copying each
  message to all of them. *(chosen)*
- **(B) External broker** (Redis, NATS, Kafka) between the DB and the clients.

**Why A won:** Simplicity and zero extra infrastructure. For a single server (and
even for several - see below), an in-memory **fan-out** is all you need, and it
keeps the whole thing runnable with one `docker compose up`. Adding a broker now
would be solving a scaling problem we don't have yet.

**What it costs:**
- The set of connected clients lives in one process's memory. It isn't *shared*
  across server instances. But this turns out **not** to matter for correctness:
  with multiple instances, each opens its own **LISTEN** connection, Postgres
  delivers every notification to all of them, and each instance fans out to its own
  clients. So every client still gets every change without any shared state.
- The genuine limit is holding a very large number of connections on the same
  boxes as the DB connections.

**When I'd switch to B:** At high scale, put **Redis Pub/Sub** or **NATS** between
the database listener and stateless edge servers so you can scale client capacity
independently of the database. Deliberately out of scope here.

---

## Decision 4 - Fire-and-forget delivery, recovered by snapshot-on-reconnect

**The alternatives were:**
- **(A) Fire-and-forget + snapshot on (re)connect.** *(chosen)*
- **(B) Guaranteed, replayable delivery** via an outbox table + per-client cursors.

**Why A won:** It's dramatically simpler and covers the realistic failure case
well. `LISTEN/NOTIFY` doesn't store past messages, so a client offline at the exact
instant of a change misses that one event. But because every client receives a full
**snapshot** as the first message on the stream when it connects or reconnects (and
the server subscribes before reading that snapshot, so nothing slips through the
gap), it's always fully correct *after* reconnecting. The only thing it can't do is
show the individual events it missed while offline.

**What it costs:**
- No history/replay of missed events. If your use case needed an auditable,
  gap-free event stream (e.g., financial ledger), this wouldn't be enough.

**The upgrade path (B):** Add an **outbox table** - every change also inserts a row
with an increasing sequence number. Each client remembers the last sequence number
it saw and, on reconnect, asks for everything newer. That gives exactly-once-ish,
replayable delivery, at the cost of storing events and extra bookkeeping. I chose
not to build it because the task doesn't call for it and it would add a lot of
complexity for a case (client offline for the precise millisecond of a change) that
the snapshot already handles correctly.

---

## Decision 5 - Send the whole changed row in the notification

**The alternatives were:**
- **(A) Send the full row as JSON in the notification.** *(chosen)*
- **(B) Send only `{id, operation}` and have clients fetch the row.**

**Why A won:** The client gets everything it needs in one message - no follow-up
query, less latency, simpler client code. Our rows are tiny, so this is free.

**What it costs:**
- `pg_notify` payloads are capped at **8000 bytes**. A row bigger than that
  wouldn't fit. For wide rows you'd use option B ("notify-then-fetch").

**When I'd switch:** The moment rows could approach that size limit.

---

## Decision 6 - Drop slow clients instead of buffering unboundedly

**The alternatives were:**
- **(A) Bounded per-client queue; drop a client whose queue fills.** *(chosen)*
- **(B) Unbounded buffering** - keep everything for every client no matter what.

**Why A won:** A single stuck or very slow client must never be able to slow down
or crash the server for everyone else. Giving each client a bounded **queue** and
dropping it if it can't keep up isolates failures. The dropped client's
`EventSource` auto-reconnects and re-fetches a snapshot, so it self-heals.

**What it costs:**
- A genuinely slow client can get disconnected and has to reconnect. That's an
  acceptable price for protecting all the healthy clients - and unbounded buffering
  would risk running the server out of memory, which is far worse.

---

## Decision 7 - Static HTML + vanilla JavaScript client, not React

**The alternatives were:**
- **(A) A single static HTML file with ~40 lines of JS using `EventSource`.**
  *(chosen)*
- **(B) A React (or similar) front-end app.**

**Why A won:** The interesting, gradeable part of this project is the real-time
*pipeline*, not the UI framework. A tiny static page shows the live updates clearly
and has **nothing to build or install** - it just works. A React app would add a
build toolchain and dependencies that distract from the point. I also included a
**CLI client** to demonstrate the stream isn't tied to any one UI.

**What it costs:**
- None of the conveniences a framework gives (component reuse, routing, state
  libraries). At this size we don't need them.

**When I'd switch:** A real product UI with many screens and interactions would
justify a framework.

---

## Decision 8 - Docker Compose to run everything

**Why:** One command (`docker compose up --build`) starts Postgres *and* the
server, wired together, with the schema loaded automatically. The person grading
doesn't need to install Python or Postgres or run migration steps. "Easy to run and
understand" is one of the stated evaluation criteria, so removing all setup
friction directly earns marks.

**What it costs:** Requires Docker installed. A no-Docker path is documented in the
README for anyone who prefers running the pieces directly.

---

## The one-sentence summary of the philosophy

Pick the simplest design that (a) provably satisfies the hard requirement - *catch
any change* - and (b) stays trivial to run and reason about, while (c) leaving a
clear, honest upgrade path (Redis fan-out, outbox replay) for the scale this task
doesn't need. Every trade-off above is an instance of that same rule.
