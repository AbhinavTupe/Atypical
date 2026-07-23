# Production notes

This is a focused project, not a full platform. Even so, the design was chosen so
that most of the awkward real-world cases are handled by the structure itself,
rather than being left as problems. This document lists the cases that are already
covered, and then the boundaries that were left out on purpose.

## Cases the design already handles

### A client joins late

When a client connects, the first message it receives is a full snapshot of all
current orders, followed by the live stream. So a client that connects an hour
after the server started still begins with correct, current data. Nothing special
has to happen for late joiners; it is how every connection works.

### A client refreshes right before a change arrives

This is the tricky timing case. A page refresh means the client disconnects and
reconnects. If the reconnect read the current state and subscribed to changes as
two separate steps, a change landing in between could slip through the gap.

The backend closes that gap by subscribing to changes before it reads the
snapshot it sends. Any change that happens in the small window is already queued
and is delivered right after the snapshot. The client de-duplicates by
`updated_at`, so seeing the same change in both the snapshot and the stream is
harmless. The result is that a refresh never loses an update.

### A client briefly loses its connection

The browser's `EventSource` reconnects on its own when a connection drops. On
reconnect it receives a fresh snapshot, so the client is correct again regardless
of what happened while it was away. The connection indicator in the dashboard
reflects this: it shows "reconnecting" and returns to "live" once the snapshot
arrives.

### Duplicate or out-of-order messages

Every order carries an `updated_at` timestamp. The client applies an incoming
change only if it is the same or newer than the version it already has. A stale or
repeated message is ignored, so clients converge on the same state.

### A change is made outside the API

Because the notification comes from a database trigger, a change made directly in
psql, by a background job, or by another service produces the same event as a
change made through the API. Clients stay in sync no matter how the data was
changed. This is verified: an update run straight against the database reaches
every connected client.

### A change in a transaction that gets rolled back

Postgres delivers a notification only when the transaction that produced it
commits. If a transaction is rolled back, no notification is sent, so clients are
never told about a change that did not actually happen.

### One slow client

Each client has its own bounded queue. A client that cannot keep up is dropped
instead of being allowed to slow the others down, and it reconnects and re-syncs
on its own. One bad client does not affect the rest.

### Running more than one backend instance

Each instance opens its own listening connection, and Postgres delivers every
notification to all listeners. So you can run several instances behind a load
balancer with no shared state and no sticky sessions, and every client still
receives every change.

## Boundaries left out on purpose

These were kept out to avoid over-building. Each has a clear place to slot in if it
is ever needed.

### Replaying the exact events missed while offline

On reconnect, a client gets a fresh snapshot, so it is always correct. What it does
not get is the list of individual events it missed while it was disconnected. For
most views that is fine, because the current state is what matters. If you needed a
gap-free, replayable history (for an audit log, say), you would add an outbox table:
every change also writes a row with an increasing sequence number, and clients ask
for everything newer than the last sequence number they saw.

### Scaling client connections independently of the database

The in-memory fan-out is fine for a single instance and for a handful of instances.
At very high connection counts you would not want many client connections sharing a
box with a database connection. The step there is to put a Redis or NATS layer
between the listener and the client-facing servers. The current design does not
block this; it is a later addition, described concretely below. For the reasoning
on why this was not done from the start, see
[`design-decisions.md`](design-decisions.md#why-pg_notify-and-not-redis-pubsub).

### Migrating fan-out to Redis

The goal of this change is to stop every backend replica from holding a Postgres
`LISTEN` connection and to let the number of client-facing servers grow
independently of the database. The source of truth stays Postgres; only the way the
notification travels changes.

The shape after the change:

```
 Postgres --NOTIFY--> [bridge process, 1 or few] --PUBLISH--> Redis
                                                                 |
   clients <--SSE-- many stateless SSE servers --SUBSCRIBE--> Redis
                                                                 
```

What changes in the code:

1. **Add a small bridge process.** It is almost exactly today's listener: it holds
   one Postgres `LISTEN orders_changes` connection, and for each notification it
   calls `redis.publish('orders_changes', payload)` instead of handing the payload
   to the in-memory hub. This is the only component that talks to Postgres for
   notifications, so Postgres sees just one (or a few) `LISTEN` connections total.
2. **Change the SSE servers to subscribe to Redis instead of Postgres.** In
   `db.py`, the `start_listener` step becomes a Redis `SUBSCRIBE orders_changes`
   instead of a Postgres `add_listener`. Everything downstream stays the same: the
   message still flows into the same `Hub`, and the `/events` endpoint is unchanged.
3. **Keep the snapshot read on Postgres.** The `/events` handler still reads the
   initial snapshot from Postgres (through the normal connection pool). Only the
   live change feed moves to Redis.

What does not change:

- The trigger and `pg_notify` in `db/init.sql` stay exactly as they are. Postgres is
  still where changes are detected, so the "catches any writer" guarantee is intact.
- The client, the SSE format, the snapshot-then-deltas flow, and the de-duplication
  logic are all untouched.

Two things to be aware of after the move: Redis pub/sub is fire-and-forget just like
`pg_notify`, so this step improves fan-out scaling but not delivery guarantees (for
replay you would use Redis Streams or an outbox, see above); and the transactional
tie between a commit and its notification now lives only in the bridge, so the bridge
should be simple and well monitored.

### Large notification payloads

The notification carries the full row, which is well within the 8 KB payload limit
for these small rows. For much larger rows you would send only the id and have the
client fetch the row.

### Authentication and authorization

The endpoints are open, which is appropriate for a demo. In a real deployment you
would authenticate clients, only stream the orders a given user is allowed to see,
restrict CORS to the real front-end origin, and serve over HTTPS. None of this
changes the core architecture.

## How this was verified

The full flow was run against a real Postgres: a client connected and received the
seed snapshot, then inserts, updates, and a delete were fired and arrived in order.
An update was also run directly against the database, bypassing the API, and it
reached the client. Finally, a second client connected late and its snapshot
correctly reflected the change that had been made directly in the database.

### Tests

That behaviour is captured in an automated test suite (`backend/tests/`) so it does
not rely on manual checking. It is intentionally small, covering the parts that
matter rather than chasing coverage numbers:

- **Hub unit tests** (`test_hub.py`): a broadcast reaches every subscriber,
  unsubscribing stops delivery, and a slow client whose queue is full is dropped
  without affecting the others. These need no database and run in milliseconds.
- **REST tests** (`test_rest.py`): listing returns the seeded orders, creating and
  updating work, an invalid status is rejected, and deleting a missing order gives
  a 404.
- **Real-time tests** (`test_realtime.py`): a client receives a snapshot on connect;
  a change made through the API is pushed to a connected client; and, the important
  one, a change made **directly in the database** (not through the API) is still
  pushed to the client.

The tests run the app in a real server and boot a throwaway Postgres automatically
(via `pgserver`), so the command is just:

```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

On Windows, or to run against an existing database, set `TEST_DATABASE_URL` to any
Postgres connection string and run `pytest`; the embedded database is then not used.
