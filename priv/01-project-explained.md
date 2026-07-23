# The Whole Project, Explained Simply

> Written assuming you know basic Python and not much else. No prior knowledge of
> databases, web servers, or "real-time" systems is assumed. Read this top to
> bottom once and you'll understand every file in the repo.

---

## 1. What are we even building?

Imagine an online shop. There's a list of orders. Each order has a customer, a
product, and a status (`pending` → `shipped` → `delivered`).

Now imagine you're staring at a web page showing that list of orders. Somewhere
else, someone changes an order (say a warehouse marks it "shipped"). **We want
your page to update by itself, instantly, without you refreshing it.**

That's the entire task: *when the data changes, everyone looking at it finds out
right away.*

The tricky word in the assignment is **"real-time"** and the phrase **"without
polling"**. Let's define both.

- **Polling** = the page keeps asking the server "any news? any news? any news?"
  every couple of seconds. It's like a kid in the back seat asking "are we there
  yet?" over and over. It works, but it's wasteful: 99% of the time the answer is
  "no". And if you ask only every 5 seconds, updates are up to 5 seconds late.
- **Real-time push** = the page asks *once*, "tell me when something happens,"
  and then just waits. The server *pushes* the news the moment it happens. No
  repeated asking. This is what we built.

---

## 2. The pieces (the cast of characters)

There are four main characters. Keep this picture in your head:

```
   YOUR BROWSER                 OUR SERVER                    THE DATABASE
  (the "client")              (Python program)              (Postgres)
        │                          │                             │
        │  "tell me when           │                             │
        │   things change" ───────▶│                             │
        │                          │  "hey Postgres, ping me     │
        │                          │   on any order change" ────▶│
        │                          │                             │
        │                          │                    (someone changes a row)
        │                          │                             │
        │                          │◀──── "an order changed!" ───│
        │◀──── "an order changed!" │                             │
        │  (page updates itself)   │                             │
```

1. **The database (Postgres)** - where the orders actually live, as rows in a
   table. Think of it as a giant, reliable Excel sheet that many programs can
   read and write at once.
2. **The server (our Python/FastAPI program)** - sits in the middle. It talks to
   the database and to all the browsers.
3. **The client (a browser page, or a command-line script)** - what a human
   looks at. It receives updates and shows them.
4. **The "notification" mechanism** - the magic wire between the database and the
   server that carries the message "something changed!". In Postgres this is
   called **LISTEN/NOTIFY** (explained below).

---

## 3. The single most important idea in this project

Here's the insight that makes this solution good instead of just okay.

**Naive idea:** In our server code, right after we save a change to the database,
we also tell all the browsers. Something like:

```python
save_order_to_database(order)   # step 1
tell_all_browsers(order)        # step 2
```

This *seems* fine. But read the assignment carefully: *"Any insert/update/delete
on this table should trigger an update."* The word is **any**.

What if someone changes an order **without going through our server**? For
example:
- An admin opens the database directly and runs a SQL command.
- A different program (a warehouse system) writes to the same database.
- A scheduled job updates a hundred orders at midnight.

In all those cases, our `tell_all_browsers()` line **never runs**, because the
change didn't go through our code. The browsers would be showing stale data and
nobody would know. That's a silent bug.

**Better idea (what we did):** Make the *database itself* announce every change.
The database is the one place that sees *all* writes, no matter who made them. So
we ask the database to shout "an order changed!" every single time a row is
inserted, updated, or deleted. Our server just listens for that shout and relays
it to the browsers.

This is why the solution is built around a **database trigger** plus
**LISTEN/NOTIFY**, instead of pushing updates from our own save function. It's the
difference between "notify on changes I know about" and "notify on *any* change".

---

## 4. How the database announces changes

Two database features make this work. (Full definitions are in
`02-concepts-explained.md`; here's the gist.)

### A trigger
A **trigger** is a small piece of logic you attach to a table that runs
*automatically* whenever the table changes. You don't call it; the database calls
it for you. Think of it like a Python decorator or an event handler: "whenever a
row in `orders` is inserted/updated/deleted, run this code."

Our trigger's job is tiny: build a little message describing what changed, and
hand it to `pg_notify`.

### pg_notify / LISTEN
Postgres has a built-in "announcement system" (a *publish/subscribe* system):
- `NOTIFY channel, 'message'` - shout a message on a named channel.
- `LISTEN channel` - subscribe so you hear those shouts.

It's like a radio station. The trigger is the DJ broadcasting on the
`orders_changes` frequency. Our server keeps a radio tuned to that frequency. The
moment the DJ speaks, our server hears it.

Put together, in `db/init.sql`:

```sql
-- when an order changes, broadcast a JSON message on 'orders_changes'
CREATE FUNCTION notify_orders_change() ... 
    PERFORM pg_notify('orders_changes', payload);   -- the "shout"

CREATE TRIGGER orders_notify
    AFTER INSERT OR UPDATE OR DELETE ON orders       -- "whenever a row changes"
    FOR EACH ROW EXECUTE FUNCTION notify_orders_change();
```

The message we broadcast is **JSON** (a text format for structured data, like a
Python dict turned into a string) that looks like:

```json
{ "operation": "UPDATE", "id": 4, "data": { "id": 4, "status": "shipped", ... } }
```

So the listener learns *what* happened (UPDATE), to *which* row (id 4), and the
*new values*.

---

## 5. How the server relays the announcement

Now the server side, which is Python. Walk through what happens when the program
starts (`backend/app/main.py` and `db.py`):

1. **Connect to the database.** We open a "pool" of connections (a set of reusable
   phone lines to Postgres) so we can run queries. See `db.py → connect()`.

2. **Start listening.** We open *one dedicated* extra connection and tell it
   `LISTEN orders_changes`. Whenever Postgres shouts, a Python function
   (`_on_notify`) runs. See `db.py → start_listener()`.

3. **Fan out to clients.** When a shout arrives, we need to pass it to *every*
   connected browser. That's the job of the **hub** (`hub.py`). The hub keeps a
   list of everyone currently connected and copies each message to all of them.

Why a separate "hub" instead of sending directly? Because there can be many
browsers connected at once. The hub is just a clean way to manage "the current
set of listeners" and hand each of them new messages. Each connected client gets
its own **queue** (a waiting line for messages) so one slow client can't hold up
the others.

---

## 6. How the message reaches the browser: SSE

The last hop is server → browser. We use **Server-Sent Events (SSE)**.

SSE is a simple, standard way for a server to push a stream of messages to a
browser over an ordinary web connection. The browser opens *one* connection and
holds it open; the server sends messages down that pipe whenever it likes. The
browser has a built-in tool called `EventSource` that handles this - including
**automatically reconnecting** if the connection drops.

Why SSE and not the more famous **WebSockets**? Because our data only flows *one
way*: server → browser. Browsers here only *receive* updates; they never send
anything back over that channel. SSE is designed exactly for one-way streams, so
it's simpler. WebSockets give you a *two-way* channel, which we'd never use - it'd
be like buying a walkie-talkie when all you need is a radio receiver. (More on
this choice in `04-tradeoffs.md`.)

In our code, the SSE endpoint is `GET /events` in `main.py`. The very first thing
a client receives on that stream is a *snapshot*: the full list of current orders.
After that it receives one message per change, like:

```
event: snapshot
data: [ {full list of all current orders} ]

data: {"operation":"UPDATE","id":4,...}

```

Sending the snapshot first is what makes a client that just loaded the page, or
one that reconnects after a refresh, start out with correct, current data. There's
one subtle detail: the server subscribes to changes *before* it reads the snapshot,
so if a change happens in the tiny gap in between, it's already queued and gets
delivered right after the snapshot. Nothing is missed. (More on this in section 10
and in `../docs/architecture.md`.)

The browser's JavaScript (`static/index.html`) loads the snapshot as its starting
state, then applies each change as it arrives. It flashes the changed row so you
can see it happen, and it also pops up a small toast notification.

---

## 7. Following one change all the way through (the full story)

Let's trace a single click. You press the "advance status" button on an order:

1. **Browser → Server.** The page sends an HTTP `PATCH /orders/4` request:
   "change order 4's status to shipped."
2. **Server → Database.** Our FastAPI code runs an SQL `UPDATE` on row 4.
3. **Inside the database.** The moment that row changes, the **trigger** fires
   automatically and calls `pg_notify('orders_changes', '{...}')`.
4. **Database → Server.** Our listening connection *hears* the notification. The
   `_on_notify` function runs and hands the message to the **hub**.
5. **Hub → all clients.** The hub copies the message into every connected client's
   queue.
6. **Server → Browsers.** Each `/events` SSE stream reads from its queue and sends
   `data: {...}` to its browser.
7. **Browser updates.** Every open page - not just the one you clicked in -
   updates its table and flashes row 4.

The beautiful part: steps 3–7 don't care *how* the change in step 2 happened. If
instead of the web page, an admin had typed an `UPDATE` directly into the
database, steps 3–7 would be **identical**. That's the whole point, and it's what
we proved works in testing.

---

## 8. The files, one line each

```
db/init.sql            The table, the trigger that shouts on changes, seed rows.
backend/app/main.py    The web server: the /events SSE stream + create/update/delete endpoints.
backend/app/db.py      Talks to Postgres: connection pool, the LISTEN loop, SQL queries.
backend/app/hub.py     Keeps the list of connected clients and copies each message to all of them.
backend/app/models.py  Shapes of the data going in/out (validation).
backend/app/config.py  Settings read from the environment (database address, etc.).
backend/static/index.html   The live web page you look at (plain HTML + JavaScript).
client/cli.py          A tiny terminal version of the client, to prove any client works.
docker-compose.yml     One command to start Postgres + the server together.
db/init.sql is loaded automatically by Postgres on first start.
```

---

## 9. How to run it and see it work

```bash
docker compose up --build
```

Then:
1. Open http://localhost:8000 in **two** browser tabs, side by side.
2. In one tab, click "Add order" or "Advance a random order".
3. Watch **both** tabs update at the same instant. Neither tab refreshed.
4. For the real "wow": open a terminal and change a row *directly in the
   database*, bypassing the web server completely:

   ```bash
   docker compose exec db psql -U postgres -d orders_db \
     -c "UPDATE orders SET status='delivered' WHERE id=1;"
   ```

   Both browser tabs still update. That change never touched our Python code - the
   database announced it and the browsers heard about it.

---

## 10. One honest limitation to know about

`LISTEN/NOTIFY` is "fire-and-forget." If a browser is **disconnected at the exact
moment** a change happens, it misses that one announcement, because there's no
recording to replay. We handle this simply: whenever a page connects or reconnects,
the stream's first message is a fresh snapshot of all current orders, so the page
is always correct after reconnecting, even if it missed a message while offline. It
just can't show you the individual events it missed. For this task that's the right
level of complexity; `04-tradeoffs.md` explains when you'd build something heavier
(an "outbox" that lets clients replay exactly what they missed).

That's the whole system. Next: `02-concepts-explained.md` defines every technical
word used here, and `03-interview-qna.md` prepares you for questions.
