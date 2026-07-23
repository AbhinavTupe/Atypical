# Interview Q&A - Likely Questions and Strong Answers

> Practice these out loud. Each answer is written so you can say it in your own
> words. Where useful, there's a short "if they push further" follow-up. Concepts
> in **bold** are defined in `02-concepts-explained.md`.

---

## A. The big-picture / design questions

### Q1. Walk me through your solution end to end.
When an order changes in Postgres, a **trigger** on the `orders` table fires
automatically and calls `pg_notify` to broadcast a small JSON message on a channel
called `orders_changes`. My FastAPI server keeps one dedicated database connection
that is **LISTEN**ing on that channel, so it receives every message. It hands the
message to an in-memory **hub**, which copies it to every connected client. Clients
are connected over **Server-Sent Events (SSE)**, so each one instantly receives the
change and updates its view. The key property is that the change detection happens
in the database, so it works no matter who made the change.

### Q2. Why did you put the change detection in the database instead of your app?
Because the requirement says *any* insert/update/delete must notify clients. If I
pushed updates from my API code, I'd only catch changes that went through my API.
Changes made by another service, a scheduled job, or an admin running SQL directly
would be missed silently. The database is the one component that sees every write,
so making it the source of truth for "something changed" is the only way to
guarantee completeness.

**If they push:** "Isn't putting logic in the DB bad?" - It's a very thin, single-
purpose trigger that only emits a notification; no business logic lives there. The
trade-off (a bit of logic in the DB, and the trigger runs on every write) is worth
the guarantee of never missing a change.

### Q3. Why SSE instead of WebSockets?
The data only flows one way here: server to client. Clients receive updates; they
never send anything back over that channel. SSE is built exactly for one-way server
push - it runs over plain HTTP, and the browser's `EventSource` gives me automatic
reconnection for free. WebSockets add a full two-way channel and a separate
protocol I would never use. I picked the simplest tool that fits. If clients later
needed to send real-time messages back (chat, collaborative editing), I'd switch to
WebSockets.

### Q4. Why not just poll?
Polling means every client repeatedly asks "anything new?" on a timer. It wastes
requests when nothing changed, and it adds lag equal to the polling interval. The
requirement explicitly says not to rely on frequent polling, and push gives instant
updates with far less traffic.

### Q5. Why Postgres / why LISTEN/NOTIFY specifically?
Postgres has this pub/sub mechanism built in, so I get real-time notifications with
zero extra infrastructure - no message broker to run. And notifications are tied to
**transactions**: a client is only notified once the change actually commits, so
they never see a change that gets rolled back. That correctness property comes for
free.

---

## B. Correctness and reliability

### Q6. What happens if a client is disconnected when a change occurs?
`LISTEN/NOTIFY` is fire-and-forget, so a client offline at that exact moment misses
that one message. I handle it with the connect flow: the stream's first message is
a full **snapshot** of current state, followed by live **deltas**. So whenever a
client connects or reconnects it re-syncs to correct state automatically. The
subtle part is I subscribe to changes on the server *before* reading that snapshot,
so a change happening in the gap is still delivered right after the snapshot and
nothing is lost. The only thing a client can't do is replay the individual events
it missed while offline. For this task that's the right level of robustness.

**If they push:** "How would you make it not miss events?" - I'd add an **outbox
table**: every change also writes a row with an incrementing sequence number.
Clients remember the last sequence number they saw, and on reconnect ask for
everything newer. That gives replayable, gap-free delivery at the cost of storing
events and more bookkeeping.

### Q7. What if the notification payload is too big?
`pg_notify` caps payloads at 8000 bytes. My rows are tiny so I send the whole row.
For large rows I'd send only the id and the operation, and have the client fetch
the full row with a follow-up query - the "notify-then-fetch" pattern.

### Q8. What if a client is slow and can't keep up?
Each client has its own bounded **queue**. If a client is so slow its queue fills
up, I drop that client rather than let it back-pressure and slow down everyone
else. The dropped client's `EventSource` will automatically reconnect and re-fetch
a snapshot. This keeps one bad client from harming the rest.

### Q9. Is there any ordering guarantee?
Within a single channel, Postgres delivers notifications in commit order, and my
per-client queue is FIFO, so a given client sees changes in a consistent order. I
also include `updated_at` on each row, so even if a client ever applied things out
of order it could tell which is newer.

### Q10. What happens if the database connection drops?
On startup I retry connecting while Postgres is still booting. For a mid-run drop,
the honest current state is that the listener connection would need to be
re-established; a production version would add automatic reconnection of the
listener plus a snapshot re-sync to clients afterward. I'd call that out as the
first hardening step.

---

## C. Code-level questions

### Q11. Why a separate dedicated connection for LISTEN, not one from the pool?
A **connection pool** hands connections out and takes them back for short queries.
The listening connection must stay open and dedicated for the entire life of the
app to keep receiving notifications, so it doesn't belong in the pool - I create it
separately and keep it.

### Q12. Why is everything `async`?
Because the server holds many long-lived open connections (one SSE stream per
client) that spend almost all their time waiting. With normal blocking code each
would tie up a thread doing nothing. **Async** lets one process efficiently juggle
thousands of mostly-idle connections using the **event loop**. FastAPI, uvicorn,
and asyncpg are all async, so the whole path is non-blocking.

### Q13. What does the hub do and why does it exist?
The hub (`hub.py`) holds the set of currently-connected clients and does
**fan-out**: it copies each incoming notification into every client's queue. It
exists to cleanly separate "receiving one message from Postgres" from "delivering
it to many clients." It also enforces the per-client bounded queue and drop-slow-
client policy.

### Q14. How does the SSE endpoint actually work in code?
`GET /events` first subscribes to the hub to get its own queue, then reads a
snapshot of all current orders and sends it as the first `snapshot` event.
Subscribing before reading the snapshot is deliberate: it closes the gap where a
change could slip through between the read and the subscription. After that, it
loops: wait for the next message on the queue (with a timeout), and either send it
as `data: {...}` or, if the timeout hits, send a `: ping` **heartbeat** to keep the
connection alive. When the client disconnects, the generator's `finally` block
unsubscribes it from the hub so we don't leak.

### Q15. How do you avoid sending stale or malformed data?
Incoming request bodies are validated by **Pydantic** models (`models.py`) - for
example `status` must be one of the three allowed values, or the request is
rejected. Outgoing changes come straight from the database row inside the trigger,
so what clients see is exactly what was committed.

### Q16. Why `AFTER` trigger and not `BEFORE`?
I only want to notify about changes that actually happened and committed. An
`AFTER` trigger runs once the row change is in place, which is the correct moment to
announce it. (I do use a separate small `BEFORE UPDATE` trigger just to stamp
`updated_at`, since that has to happen before the row is written.)

### Q17. Why `FOR EACH ROW`?
So the trigger fires once per changed row and I can include that row's data in the
notification. A statement-level trigger would fire once for a multi-row statement
without easy access to each row.

---

## D. Scaling questions

### Q18. Does this work with multiple server instances behind a load balancer?
Yes, with no changes. Each server instance opens its own `LISTEN` connection.
Postgres delivers every `NOTIFY` to *all* listening connections, so every instance
receives every change and fans it out to whichever clients happen to be connected
to it. No shared state and no sticky sessions are required.

### Q19. What breaks first as you scale up, and how would you fix it?
The bottleneck is holding a huge number of SSE connections on the same boxes that
also hold database connections. The fix is to put a lightweight pub/sub layer
(**Redis Pub/Sub** or **NATS**) between the database listener and the front-end
servers: one small process listens to Postgres and republishes to Redis; many
stateless edge servers subscribe to Redis and hold the client connections. That
lets you scale client-facing capacity independently of the database.

### Q20. How many clients can this handle as-is?
A single async Python process can comfortably hold thousands of mostly-idle SSE
connections, because each one is cheap when it's just waiting. Keep three limits in
mind, in plain terms (you don't need exact numbers): how many connections one
process can hold open (bounded by memory and OS file limits), how much fan-out work
it can do (roughly connections times the change rate, since every change is written
to every client), and the write side, since every change goes through one Postgres
`NOTIFY`. You grow the first two by adding more backend instances; the Postgres
write rate is the shared ceiling that adding instances does not raise.

### Q20b. Why did you use Postgres pg_notify instead of Redis pub/sub?
This is the question to be ready for, because Redis is the reflex answer. Say three
things:

1. **pg_notify is tied to the database transaction.** It only fires when the write
   commits, and it fires for *any* writer, including changes made outside the API.
   That is exactly the requirement. Redis pub/sub knows nothing about a Postgres
   transaction, so on its own it cannot give that guarantee.
2. **Redis would not remove a real limitation here.** The usual reason to add Redis
   is "shared state for stateless replicas," but my replicas are already stateless:
   each just runs `LISTEN` and Postgres delivers every change to all of them. Which
   client sits on which replica does not need to be shared, because every replica
   already gets every change.
3. **To feed Redis correctly you would still need Postgres as the source.** You'd
   put a bridge that listens to Postgres and republishes to Redis. So Redis does not
   replace this design; it sits downstream of it. And plain Redis pub/sub is also
   fire-and-forget, so it does not improve delivery guarantees; that would be Redis
   Streams or Kafka.

Then show you know when it *does* win: "Redis is a scaling step, not a correctness
upgrade. Once fan-out outgrows one process, a Redis layer lets many stateless SSE
servers hold client connections while a single bridge talks to Postgres, so client
capacity scales independently of the database. That is when I'd add it, and the
migration is straightforward because the source of truth stays Postgres."

If asked "so was Redis the wrong choice?" answer: "Not wrong, just premature for
this scope. Using it by default would add infrastructure without solving a problem I
have yet. Knowing exactly when to reach for it is the point."

### Q20c. Would Redis Streams or Kafka change your answer?
Yes, for a different reason. Those add durability and replay: a client that was
offline could ask for everything it missed. That solves the one real limitation of
this design (no event replay). If the requirement were an auditable, gap-free event
history, I would reach for a durable log rather than any fire-and-forget pub/sub,
Redis or Postgres. For live UI state, which is this task, it is not needed.

---

## E. "What would you improve / what's missing" questions

### Q21. What would you add with more time?
In priority order: (1) automatic reconnection of the listener connection with a
client re-sync; (2) an outbox table for gap-free, replayable delivery; (3)
authentication/authorization on the endpoints; (4) restricting **CORS** to the real
front-end origin; (5) tests in the repo (I verified the full flow manually against
a real Postgres, but I'd add automated integration tests); (6) metrics/logging for
number of subscribers and delivery latency.

### Q22. How did you test it?
I ran the whole stack against a real Postgres: a client connected and received the
seed snapshot, then I fired an insert, an update, and a delete and confirmed each
arrived in order. I also made an update **directly in the database**, bypassing the
API entirely, and confirmed clients still received it, which proves the central
claim that it catches *any* change, not just API writes. Finally I connected a
second client *late*, after those changes, and confirmed its snapshot already
reflected the direct-database change, which proves late joiners get correct state.

### Q22b. What happens if a user refreshes the page right as a change happens, or joins late?
Both are covered by the connect flow. Every connection starts with a full snapshot
of current state, so a late joiner is immediately correct. For the refresh race,
the server subscribes to changes before it reads the snapshot, so a change landing
in that window is delivered right after the snapshot instead of being lost. The
client de-duplicates by `updated_at`, so if it happens to see the same change in
both the snapshot and a delta, it applies it once. I verified the late-joiner case
in testing.

### Q23. What's the single biggest limitation?
The fire-and-forget delivery: a client offline at the moment of a change misses
that specific event (mitigated by snapshot-on-reconnect, but not replayed). It's a
deliberate simplicity trade-off, and the outbox pattern is the known fix.

### Q24. Security considerations?
Right now it's an open demo. In production I'd add authentication, authorize which
orders a given user may see (and only stream those), lock down CORS, run over
HTTPS, and validate/limit request sizes and rates. None of that changes the core
architecture.

---

## F. Rapid-fire definitions they might spot-check

- **Trigger** - code in the database that runs automatically on table changes.
- **LISTEN/NOTIFY** - Postgres's built-in publish/subscribe messaging.
- **SSE** - one-way server→client streaming over HTTP; browser reconnects itself.
- **Fan-out** - copying one message to many recipients.
- **Connection pool** - reusable set of open DB connections for short queries.
- **Transaction** - all-or-nothing group of DB changes; NOTIFY delivers on commit.
- **Async/event loop** - one process juggling many waiting connections efficiently.
- **Snapshot + delta** - load full state once, then apply streamed individual changes.
- **Idempotent reconnect** - on reconnect, refetch snapshot so state is correct
  regardless of what was missed.

---

If you can comfortably answer A1–A5, B6, C11–C14, and D18–D19 in your own words,
you're covered for the great majority of what they'll ask.
