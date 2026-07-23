# Real-time Orders

A small backend service that pushes order changes to clients the moment they
happen in the database, without the clients having to poll for updates.

**Stack:** Python, FastAPI, PostgreSQL (LISTEN/NOTIFY + triggers), Server-Sent Events.

This README is a quick overview. The detailed write-ups live in the
[`docs/`](docs/) folder:

- [`docs/architecture.md`](docs/architecture.md) - how the pieces fit together and how data flows.
- [`docs/design-decisions.md`](docs/design-decisions.md) - the choices made and the trade-offs behind them, including why `pg_notify` was chosen over Redis pub/sub.
- [`docs/production-notes.md`](docs/production-notes.md) - the real-world cases the design already handles, and where you'd extend it (including how to move fan-out to Redis).
- [`docs/api-reference.md`](docs/api-reference.md) - the endpoints.

## What it does

The task was: whenever the data in the database changes, connected clients should
find out right away. The important detail is in how the change is detected.

In this project, a change to the `orders` table fires a notification from inside
the database itself. So an update made through the API triggers an event, and an
update made straight to the database (in psql, by another service, or by a
scheduled job) triggers the exact same event. The clients don't know or care
which path the change took.

This matters in a few real situations: a background job that updates orders at
night, an admin fixing a row by hand, or a second service writing to the same
table. In all of those, the clients still stay in sync, because the database is
the thing announcing the change, not the API.

## Run it

```bash
docker compose up --build
```

Then:

- Open http://localhost:8000 to see the live dashboard. Open a second tab and
  watch both stay in sync.
- Run the terminal client to see the same stream in your console:

  ```bash
  python client/cli.py
  ```

- To see that the notification really comes from the database and not the API,
  change a row directly with psql and watch every open client update:

  ```bash
  docker compose exec db psql -U postgres -d orders_db \
    -c "UPDATE orders SET status='delivered' WHERE id=1;"
  ```

  That update never went through the API, but every client still gets notified.

## Tests

There is a small test suite covering the parts that matter: the fan-out hub, the
REST endpoints, and the two real-time guarantees (a client gets a snapshot on
connect, and a change made directly in the database still reaches connected
clients). The tests boot a throwaway Postgres on their own, so there is nothing to
set up:

```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

On Windows, or to run against an existing database, set `TEST_DATABASE_URL` to any
Postgres and run `pytest`. More detail is in
[`docs/production-notes.md`](docs/production-notes.md#tests).

## How it works, briefly

```
 browser / CLI  --HTTP-->  FastAPI  --SQL-->  PostgreSQL
                                                 |
                                     a trigger fires on any change
                                                 |
                                    pg_notify('orders_changes', ...)
                                                 |
   all clients  <--SSE--  FastAPI (LISTEN)  <----+
```

1. A trigger on the `orders` table runs on every insert, update, and delete, and
   sends a notification on a Postgres channel.
2. The backend keeps one connection open that is listening on that channel, so it
   receives every change.
3. It passes each change on to all connected clients over Server-Sent Events.

When a client connects, the first thing it receives is a full snapshot of the
current orders, followed by a live stream of changes. This is what keeps a client
that just refreshed, or one that joins late, showing correct and current data.
See [`docs/architecture.md`](docs/architecture.md) for the details.

## Project layout

```
.
├── db/
│   └── init.sql              # orders table, the notify trigger, seed rows
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI app: the /events stream and the orders API
│   │   ├── db.py             # Postgres pool, queries, and the LISTEN loop
│   │   ├── hub.py            # keeps the connected clients and sends each one every change
│   │   ├── models.py         # request/response shapes and validation
│   │   └── config.py         # settings read from the environment
│   ├── static/index.html     # the live dashboard (Tailwind + Sonner)
│   ├── tests/                # pytest: hub, REST, and real-time guarantees
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── Dockerfile
├── client/
│   └── cli.py                # a small terminal client, standard library only
├── docs/                     # architecture, design decisions, production notes, API
├── docker-compose.yml
└── README.md
```

## Using your own Postgres instead of Docker

The backend connects to whatever `DATABASE_URL` points at, so you can skip Docker
entirely and use any Postgres: one installed locally, or a hosted one like Supabase,
Neon, RDS, or similar.

Two one-time steps: apply the schema, then run the backend against it.

```bash
# 1. Apply the schema (table + trigger + seed data) to your database.
#    Use the connection string for your own Postgres here.
psql "postgresql://USER:PASSWORD@HOST:5432/DBNAME" -f db/init.sql

# 2. Run the backend pointed at the same database.
cd backend
pip install -r requirements.txt
export DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/DBNAME"
uvicorn app.main:app --reload
```

Then open http://localhost:8000 as before. Notes:

- The connection string format is
  `postgresql://user:password@host:port/dbname`. For a plain local install that is
  often `postgresql://postgres:postgres@localhost:5432/orders_db`.
- On Windows, set the variable with `set DATABASE_URL=...` (Command Prompt) or
  `$env:DATABASE_URL="..."` (PowerShell) instead of `export`.
- Nothing else needs to change. `LISTEN/NOTIFY` is a core Postgres feature, so any
  standard Postgres 12 or newer works, local or hosted.
- You can also mix the two: run only Postgres in Docker but the backend locally, or
  the reverse, as long as both use the same `DATABASE_URL`.
