# Every Concept, Explained

> A dictionary for this project. If you hit a word in the code or the README you
> don't know, it's defined here. Written for someone who knows Python and little
> else. Ordered roughly from "most basic" to "most specific to this project."

---

## Client and server

A **server** is a program that runs and waits for requests, then responds. A
**client** is a program that makes requests. Your browser is a client; when you
open a website, it asks a server for the page and the server sends it back. In
this project, our FastAPI Python program is the server, and the browser page (and
the CLI script) are clients.

## HTTP

**HTTP** is the language clients and servers speak over the web. A client sends a
**request** ("GET me the page at /orders") and the server sends a **response**
(the data + a status code like `200 OK` or `404 Not Found`). Each request usually
names a **method**:

- `GET` - fetch something (read-only).
- `POST` - create something new.
- `PATCH` - modify part of something that exists.
- `DELETE` - remove something.

Our server offers `GET /orders`, `POST /orders`, `PATCH /orders/{id}`,
`DELETE /orders/{id}`, and the special streaming `GET /events`.

## API

**API** = Application Programming Interface. In web terms it just means "the set
of URLs (endpoints) a server offers and the rules for using them." Our API is the
list of methods+paths above. When we say "through the API," we mean "by making an
HTTP request to our server," as opposed to talking to the database directly.

## Endpoint / route

A single URL your server responds to, e.g. `/orders`. In FastAPI you define one
with a decorated function:

```python
@app.get("/orders")      # this function handles GET requests to /orders
async def list_orders():
    ...
```

## JSON

**JSON** is a text format for structured data. It looks almost exactly like a
Python dict/list written out:

```json
{ "id": 4, "customer_name": "Ada", "status": "shipped" }
```

It's how the browser and server exchange structured data, and it's what our
database notification message is written in. Python can convert between dicts and
JSON with the `json` module (`json.dumps`, `json.loads`).

## Database

A **database** is a program dedicated to storing data reliably and letting many
other programs read/write it at once, safely. We use **PostgreSQL** (aka
"Postgres"), a popular free one. Data lives in **tables**.

## Table, row, column

A **table** is like a sheet in Excel. Each **column** is a field (e.g.
`customer_name`), and each **row** is one record (one order). Our `orders` table
has columns `id`, `customer_name`, `product_name`, `status`, `updated_at`, and
each order is a row.

## Primary key

A **primary key** is a column whose value uniquely identifies each row - no two
rows share it. Our `id` column is the primary key. `SERIAL` means Postgres
auto-assigns the next number (1, 2, 3, …) so you don't have to.

## SQL

**SQL** (Structured Query Language) is the language you use to talk to a
relational database like Postgres. A few statements we use:

```sql
SELECT * FROM orders;                              -- read all rows
INSERT INTO orders (customer_name, ...) VALUES (...);  -- add a row
UPDATE orders SET status = 'shipped' WHERE id = 4; -- change a row
DELETE FROM orders WHERE id = 4;                   -- remove a row
```

`INSERT`, `UPDATE`, `DELETE` are the three ways to *change* data - the three
things our trigger reacts to.

## Transaction

A **transaction** is a group of database changes that either *all* succeed or
*all* get undone - never half-done. Like a bank transfer: subtract from one
account and add to another must both happen or neither. Important detail for us:
a `NOTIFY` is only actually delivered when its transaction **commits** (finishes
successfully). So clients never get told about a change that later got rolled
back. This is a reliability win we get for free.

## Trigger

A **trigger** is code stored *inside the database* that runs automatically when a
table changes. You attach it to a table and specify when it fires (before/after
insert/update/delete). You never call it yourself - the database does. Analogy: a
Python event handler or a `@property` setter that runs whenever a value changes.

Ours fires *after* any insert/update/delete on `orders` and calls `pg_notify`.

## Stored function (PL/pgSQL)

To do anything non-trivial in a trigger, you write a small **function** inside the
database using a language called **PL/pgSQL** (Postgres's own mini-language, a bit
like Python but for SQL). Our function `notify_orders_change()` builds the JSON
message and calls `pg_notify`. The trigger just says "run this function on every
change."

## LISTEN / NOTIFY (Postgres pub/sub)

Postgres has a built-in **publish/subscribe** ("pub/sub") messaging system:

- **`NOTIFY channel, 'message'`** - publish a short text message on a named
  channel (we use the channel name `orders_changes`).
- **`LISTEN channel`** - subscribe; from now on you receive messages sent on that
  channel.
- **`pg_notify('channel', 'message')`** - the function form of `NOTIFY`, used
  inside our trigger function.

Mental model: a radio station. `NOTIFY` broadcasts; anyone doing `LISTEN` on that
frequency hears it. Our trigger broadcasts on every change; our server listens.
**Pub/sub** just means "publishers don't know or care who's listening; subscribers
just tune in." It decouples the two sides.

## Publish/Subscribe (pub/sub) - the general pattern

A messaging pattern where **publishers** send messages to a **channel** (or
"topic") without knowing who will receive them, and **subscribers** register
interest in a channel and receive whatever is published there. `LISTEN/NOTIFY` is
Postgres's built-in version. The point is loose coupling: you can add or remove
subscribers without changing the publisher.

## Polling vs. push

- **Polling**: the client repeatedly asks "anything new?" on a timer. Simple but
  wasteful and laggy.
- **Push**: the client subscribes once and the server sends data the moment it's
  available. Efficient and instant. This project is push-based; avoiding polling
  is an explicit requirement.

## Real-time

Loosely, "the user finds out about a change within a fraction of a second of it
happening, automatically." It doesn't mean nanosecond guarantees - it means no
manual refresh and no multi-second polling lag.

## Server-Sent Events (SSE)

**SSE** is a standard way for a server to push a continuous stream of messages to
a client over a single, long-lived HTTP connection. The client opens the
connection once; the server keeps it open and writes messages whenever it wants,
each formatted like:

```
data: {"operation":"UPDATE","id":4}

```

(Note the blank line - that's how the browser knows one message ended.) It's
**one-directional**: server → client only. Browsers have a built-in client for it
called `EventSource`, which also **auto-reconnects** if the connection drops. We
use SSE to push order changes to the page.

## EventSource

The browser's built-in JavaScript object for consuming an SSE stream:

```javascript
const es = new EventSource("/events");
es.onmessage = (e) => { /* e.data is the message text */ };
```

You give it a URL, and it calls your function every time the server sends a
message. It handles reconnection for you.

## WebSockets (and why we didn't use them)

**WebSockets** are another push technology, but they create a **two-way** channel:
both client and server can send messages at any time. Great for chat, games,
collaborative editing. We didn't use them because our data only flows one way
(server → client). SSE does exactly that with less complexity. Choosing SSE here
is a deliberate "use the simplest tool that fits" decision (see `04-tradeoffs.md`).

## FastAPI

A **web framework** for Python - a library that makes it easy to define endpoints,
read request data, validate it, and return JSON. You write functions and decorate
them with `@app.get(...)`, `@app.post(...)`, etc. FastAPI is built for `async`
code (below), which suits our long-lived streaming connections well.

## Uvicorn

FastAPI is just the code that *describes* your app; you need a program to actually
run it and handle incoming network connections. **Uvicorn** is that program (an
"ASGI server"). The command `uvicorn app.main:app` means "run the `app` object
found in `app/main.py`."

## Asynchronous programming (async / await)

Normally Python runs one line after another and *blocks* (waits, doing nothing
else) whenever it's waiting on something slow, like the network. That's a problem
when you have hundreds of clients each holding an open connection - you can't
dedicate a whole thread to each doing nothing.

**Async** lets one program juggle many waiting tasks efficiently. Functions
defined with `async def` can `await` a slow operation, and while they wait, Python
goes and runs other tasks. Think of one waiter serving many tables: instead of
standing frozen at one table waiting for diners to decide, they take an order,
move on, and come back. This is why every function touching the network here is
`async`, and why FastAPI + asyncpg are async libraries.

## Event loop

The "waiter" from the analogy above has a name: the **event loop**. It's the
scheduler that runs your async tasks, pausing ones that are waiting and resuming
ones that are ready. You rarely interact with it directly; `asyncio.run(...)`
starts one. `asyncio.create_task(...)` hands it a new task to juggle (we use this
to broadcast a notification without blocking).

## asyncpg

The Python **library** we use to talk to Postgres, built for async code. It runs
SQL queries and - crucially for us - supports `add_listener(...)`, which lets us
receive `LISTEN/NOTIFY` messages as they arrive. (An alternative, `psycopg`, works
too; asyncpg is fast and async-native.)

## Connection pool

Opening a fresh connection to a database is relatively slow. A **connection pool**
keeps a handful of connections open and ready, and hands one out when you need to
run a query, then takes it back. Like a pool of taxis waiting at a rank instead of
calling a new one each time. We create one with `asyncpg.create_pool(...)`. Note:
our *listening* connection is separate and long-lived, not from the pool, because
it must stay dedicated to `LISTEN` for the app's whole life.

## Queue (asyncio.Queue)

A **queue** is a first-in-first-out waiting line for data: you `put` items in one
end and `get` them from the other, in order. We give each connected client its own
`asyncio.Queue`. When a notification arrives, the hub `put`s it into every client's
queue; each client's SSE loop `get`s from its own queue and sends it on. A
**bounded** queue has a maximum size; if a client is too slow and its queue fills
up, we drop that client rather than let it slow everyone down.

## Fan-out

Taking one incoming message and copying it to many recipients. Our **hub** does
fan-out: one database notification → copied into every connected client's queue.

## The Hub (in this project)

Our own small class (`hub.py`) that holds the set of currently-connected clients
and does the fan-out. It's "in-memory," meaning the list lives in the running
program's RAM (not saved anywhere). Simple and fast; the trade-off is discussed in
`04-tradeoffs.md`.

## Pydantic / models / validation

**Validation** means checking that incoming data has the right shape and types
before you trust it (e.g., `status` must be one of `pending`/`shipped`/
`delivered`). **Pydantic** is the library FastAPI uses for this; you declare the
expected shape as a class (`models.py`) and FastAPI automatically rejects bad
requests with a clear error. It also converts things like timestamps to JSON for
responses.

## Environment variables / config

**Environment variables** are settings passed to a program from outside it,
instead of hard-coded in the source. For example, the database address lives in
`DATABASE_URL`. This lets the same code run against a local database or a
production one just by changing the variable. Our `config.py` reads these with
`os.getenv(...)`.

## Docker and docker-compose

**Docker** packages a program plus everything it needs to run (the right Python
version, libraries, etc.) into a **container** - a self-contained box that runs the
same on any machine. This avoids "works on my computer" problems.
**docker-compose** describes *several* containers and how they connect, in one
file. Ours defines two: the Postgres database and our Python server, wired
together, startable with a single `docker compose up` command. This is why the
grader can run the whole thing without installing Python or Postgres themselves.

## Heartbeat

A tiny message sent periodically over an idle connection just to prove it's still
alive, so that browsers, proxies, or firewalls don't close it for being quiet. In
our SSE stream, if nothing happens for a while we send a comment line (`: ping`)
as a heartbeat.

## Snapshot vs. delta

- A **snapshot** is the full current state (all orders right now). The stream
  sends one as its very first message when a client connects (there is also a
  plain `GET /orders` for simple clients that don't consume the stream).
- A **delta** is a description of a single change ("order 4 became shipped"). The
  SSE stream sends deltas after the snapshot.

Combining them is a common real-time pattern: load one snapshot, then keep it
up-to-date by applying deltas as they stream in. Doing it over a single stream
(snapshot first, deltas after) avoids the gap you'd get from fetching the
snapshot and subscribing to changes as two separate steps.

## CORS

**CORS** (Cross-Origin Resource Sharing) is a browser security rule about which
web pages are allowed to call which servers. Because our setup is simple/demo, we
allow all origins. In production you'd restrict it to your real front-end's
address.

---

If a term you need isn't here, it's probably a combination of two that are - check
`01-project-explained.md` for how they fit together, or `03-interview-qna.md` for
how they come up in questions.
