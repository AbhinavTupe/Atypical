# Architecture

This document explains how the system is put together and, more importantly, how
data moves through it.

## The goal

When an order changes in the database, every connected client should see the
change almost immediately, and clients should not have to poll for it. The change
must be picked up no matter how it was made: through the API, or directly in the
database.

## The components

```
 browser / CLI  --HTTP-->  FastAPI  --SQL-->  PostgreSQL
                                                 |
                                     trigger fires on any change
                                                 |
                                    pg_notify('orders_changes', ...)
                                                 |
   all clients  <--SSE--  FastAPI (LISTEN)  <----+
```

**PostgreSQL** holds the `orders` table and does the change detection. A trigger
on the table runs on every insert, update, and delete, and calls `pg_notify` to
publish a small JSON message on a channel named `orders_changes`. Because the
trigger lives in the database, it fires for every writer, including ones that
never touch the API.

**FastAPI backend** does two jobs. It exposes a normal REST API for creating,
updating, and deleting orders, and it keeps one dedicated connection open that is
listening on the `orders_changes` channel. When a notification arrives, the
backend forwards it to every connected client.

**The hub** (`backend/app/hub.py`) is a small in-memory object that holds the set
of currently connected clients and copies each incoming change to all of them.
Each client has its own bounded queue, so one slow client cannot hold up the
others.

**Clients** connect over Server-Sent Events (SSE). The browser dashboard and the
terminal client both open a single long-lived connection and receive changes as
they happen.

## How a single change flows through

Take a status change as an example:

1. A client sends `PATCH /orders/4` with the new status.
2. The backend runs an SQL `UPDATE` on row 4.
3. The moment that row changes, the trigger fires and calls
   `pg_notify('orders_changes', '{...}')` with the new row as JSON.
4. The backend's listening connection receives the notification and hands it to
   the hub.
5. The hub copies it into every connected client's queue.
6. Each client's SSE stream reads from its queue and sends the change down to the
   browser or terminal.
7. Every open client updates, not just the one that made the change.

Steps 3 through 7 do not depend on step 2 going through the API. If the same
`UPDATE` had been typed directly into psql, steps 3 through 7 would be identical.
That is the core property of the design.

## Connect flow: snapshot then live changes

A live stream of changes is not enough on its own. A client that connects at 10:05
needs to know the state as of 10:05, not just what changes after it. It also needs
to be correct if it reconnects after a brief drop or a page refresh.

The stream handles this by sending two kinds of messages on the same connection:

1. First, a `snapshot` event containing every current order.
2. Then, a `data` event for each change that happens afterward.

So a client's logic is simple: load the snapshot as the starting state, then keep
it up to date by applying each change as it streams in.

### Closing the gap between snapshot and stream

There is a subtle race to be careful about. If you read the snapshot first and
subscribe to the stream second, a change that happens in between would be missed:
it is not in the snapshot yet, and you were not subscribed when it was announced.

The backend avoids this by subscribing to the hub **before** it reads the
snapshot. If a change lands in that small window, it is already sitting in the
client's queue and gets delivered right after the snapshot. The only side effect
is that the client might see the same change twice: once in the snapshot and once
as a delta. That is harmless, because the client de-duplicates (see below).

The relevant code is in `backend/app/main.py`, in the `/events` handler.

### De-duplication and ordering on the client

Each order carries an `updated_at` timestamp. When a change arrives, the client
compares it to the version it already has and only applies it if it is the same or
newer. This means:

- A duplicate (the same change seen in both the snapshot and a delta) is applied
  once and then ignored the second time.
- An out-of-order or stale message never overwrites newer data.
- A late joiner and an early joiner converge on the same state.

Deletes are always applied, since a delete has no newer version to compare
against.

## Multiple backend instances

The design works unchanged if you run several copies of the backend behind a load
balancer. Each instance opens its own listening connection. Postgres delivers
every notification to all listeners, so each instance receives every change and
forwards it to whichever clients happen to be connected to it. There is no shared
state between instances and no need for sticky sessions.

## Why the listening connection is separate from the pool

The backend uses a connection pool for short-lived queries (the REST endpoints
borrow a connection, run a query, and return it). The listening connection is
different: it must stay open and dedicated for the whole life of the process so it
never stops receiving notifications. For that reason it is created on its own and
kept, rather than being taken from the pool.

## File map

| File | Responsibility |
|------|----------------|
| `db/init.sql` | The `orders` table, the trigger and its function, seed data. |
| `backend/app/main.py` | The FastAPI app: the `/events` SSE stream and the orders REST API. |
| `backend/app/db.py` | Connection pool, CRUD queries, and the LISTEN background task. |
| `backend/app/hub.py` | Holds connected clients and fans each change out to all of them. |
| `backend/app/models.py` | Request and response shapes, with validation. |
| `backend/app/config.py` | Settings read from environment variables. |
| `backend/static/index.html` | The live browser dashboard. |
| `client/cli.py` | A terminal client that reads the same stream. |
