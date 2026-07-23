# Design decisions and trade-offs

Each decision below lists what it gives us and what it costs, so the reasoning is
easy to follow and easy to question.

## Detect changes in the database, not in the API

The requirement is that any change to the `orders` table notifies clients. The
simplest approach would be to have the API notify clients right after it writes to
the database. The problem is that this only catches changes that go through the
API. A change made by a background job, another service, or an admin running SQL
by hand would be missed, and clients would quietly show stale data.

The database is the one place that sees every write. So the change detection lives
there, as a trigger that fires on every insert, update, and delete. This is the
only approach that actually satisfies "any change."

What it costs: a small, single-purpose trigger now lives in the database, and it
runs on every write. The trigger contains no business logic, only the notification,
so the cost is small and the guarantee is worth it.

There is also a nice bonus. A Postgres notification is tied to the transaction that
caused it, and is only delivered once that transaction commits. So clients are
never told about a change that later gets rolled back.

## Why pg_notify and not Redis pub/sub

This is the decision most worth explaining, because a pub/sub system like Redis is
the reflex answer for "push updates to many clients," and it is a reasonable
question to ask. For this project, `pg_notify` is the better fit. Here is the full
reasoning.

**Why pg_notify was chosen**

- **It is tied to the database transaction.** A `pg_notify` fires only when the
  writing transaction commits, and it carries the committed row. That gives two
  things for free: clients are never told about a change that later rolls back, and
  there is no window where the notification and the actual data disagree. Redis
  pub/sub has no knowledge of a Postgres transaction, so it cannot offer this.
- **It captures every writer.** Because the notification comes from a trigger
  inside the database, it fires for any change: through the API, from a background
  job, from another service, or from someone typing SQL by hand. This is the core
  requirement of the task, and it is satisfied without any extra moving parts.
- **It needs no extra infrastructure.** No broker to deploy, secure, monitor, or
  keep in sync with the database. The whole system is Postgres plus the app.

**Why not Redis pub/sub right now**

- **It would not remove a real limitation here.** The usual argument for Redis is
  "shared state so replicas can be stateless." But the replicas in this design are
  already effectively stateless: each one just runs `LISTEN`, and Postgres delivers
  every notification to every listener. The thing that is not shared, which client
  is connected to which replica, does not need to be shared, because every replica
  already receives every change.
- **It cannot be the source of truth on its own.** To publish correct events into
  Redis you would still need something reading committed changes out of Postgres (a
  trigger and listener bridge, or change data capture). So Redis does not replace
  the mechanism here; it would sit downstream of it.
- **Plain Redis pub/sub is also fire-and-forget.** It does not persist messages or
  let a client replay what it missed, which is the same delivery model we already
  have. It is Redis Streams (or Kafka), not Redis pub/sub, that would add
  durability and replay. So reaching for Redis pub/sub specifically would not
  improve delivery guarantees.

**When Redis (or NATS) does win, and we would switch**

Redis earns its place as a scaling step, not a correctness upgrade. Switch when:

- **Fan-out outgrows a single process.** When the number of connected clients times
  the change rate is more than one backend process can serialize and write, you want
  to spread client connections across many stateless servers. A Redis layer lets one
  small bridge listen to Postgres and republish, while a fleet of SSE servers
  subscribe to Redis and hold the client connections. Client capacity then scales
  independently of the database.
- **You want to stop every replica holding a Postgres `LISTEN` connection.** At many
  replicas, funnelling them all through a single Redis bridge keeps Postgres
  connection count low.
- **You are already running Redis** for other reasons, so it is not new
  infrastructure.

The important framing: the source of truth stays Postgres either way. Redis changes
*how the notification is fanned out*, not *how the change is detected*. The concrete
steps for that migration are in
[`production-notes.md`](production-notes.md#migrating-fan-out-to-redis).

## Server-Sent Events instead of WebSockets

The data here flows one way: the server pushes changes and clients receive them.
Clients never send messages back over that channel; they make ordinary REST calls
for writes.

Server-Sent Events fit this shape exactly. They run over plain HTTP, and the
browser's built-in `EventSource` reconnects on its own if the connection drops.
WebSockets would add a two-way channel and a separate protocol that this project
would never use.

What it costs: SSE cannot carry client-to-server messages on the same channel, and
older browsers support it less well. Neither matters here. If a genuine two-way
real-time need appeared later (a chat feature, live collaboration), WebSockets
would be the right switch.

## Send a snapshot first, then live changes

A client needs to know the current state when it connects, not just future
changes. So the stream sends a full snapshot as its first message, and then a
stream of individual changes.

To avoid a gap between the snapshot and the stream, the backend subscribes to
changes before it reads the snapshot. Anything that happens in the small window in
between is delivered right after the snapshot. Combined with client-side
de-duplication by `updated_at`, this means a client that just refreshed, or one
that joins late, ends up correct without any special handling. See
[`production-notes.md`](production-notes.md) for the specific cases this covers.

What it costs: a client may occasionally receive the same change twice, once in
the snapshot and once as a delta. The de-duplication makes this a non-issue.

## An in-memory hub for fan-out

The set of connected clients is held in memory in the backend process, and each
change is copied to all of them. This needs no extra infrastructure and keeps the
whole thing runnable with a single command.

What it costs: the list of clients is per-process and not shared between instances.
This turns out not to matter, because when you run several instances each one has
its own listening connection and receives every change anyway (see
[`architecture.md`](architecture.md)). If you later needed to scale client
connections independently of the database, you would put a Redis or NATS layer
between the listener and the client-facing servers. That is a deliberate later
step, not something this scale needs.

## Drop a slow client rather than buffer without limit

Each client has a bounded queue. If a client is so slow that its queue fills up, it
is dropped instead of being allowed to slow everyone else down. Its `EventSource`
reconnects on its own and receives a fresh snapshot, so it recovers cleanly.

What it costs: a very slow client can be disconnected and has to reconnect. That is
a good trade compared to letting one stuck client consume unbounded memory or
degrade the service for everyone.

## Send the whole row in the notification

The notification carries the full changed row as JSON, so the client has
everything it needs without a follow-up query. The rows here are small, so this is
free.

What it costs: Postgres caps a notification payload at 8 KB. For rows that could
approach that size, you would send only the id and let the client fetch the row.
The rows in this project are nowhere near that limit.

## A static HTML dashboard instead of a framework build

The interesting part of this project is the real-time pipeline, not the UI
tooling. The dashboard is a single HTML file that uses Tailwind and Sonner from a
CDN, so there is nothing to build or install. A terminal client is included as well,
to show the stream is not tied to any one UI.

What it costs: none of the conveniences a front-end framework provides. At this
size they are not needed.

## Docker Compose to run everything

One command starts Postgres and the backend together, with the schema loaded
automatically. Whoever runs it does not need to install Python or Postgres or run
any setup steps. A no-Docker path is documented for anyone who prefers to run the
parts directly.
