# Tests - What They Cover and How to Talk About Them

> The tests are a small, deliberate touch. Their job is not full coverage; it is to
> show you thought about correctness and can prove the system's main claims. This
> file explains what is there and how to describe it if asked.

---

## What exists

There are 11 tests in `backend/tests/`, split into three files:

**`test_hub.py` - unit tests, no database, run in milliseconds.**
These test the fan-out hub on its own:
- a broadcast reaches every subscriber,
- unsubscribing stops delivery,
- a slow client whose queue is full is dropped, and the healthy client still gets
  the message.

The last one is the interesting one: it proves the "one slow client can't stall the
others" claim in code.

**`test_rest.py` - the API basics.**
Listing returns the seeded orders, creating and updating work, an invalid status is
rejected with a 422, and deleting a missing order returns 404. Nothing fancy, just
proof the endpoints behave.

**`test_realtime.py` - the important ones.**
These prove the two headline claims:
- a client receives a full snapshot the moment it connects,
- a change made through the API is pushed to a connected client,
- a change made **directly in the database**, bypassing the API, is still pushed to
  the client.

That third test is the one to point at. It is the whole thesis of the project,
written as an assertion that either passes or fails.

---

## How they run (worth understanding, in case they ask)

- The tests start the actual app in a real server and talk to it over HTTP. That is
  deliberate: Server-Sent Events are about a real connection that really opens and
  closes, so testing over a real server is more honest than faking the transport
  in-process.
- They boot a throwaway PostgreSQL automatically using a package called `pgserver`,
  so whoever runs the tests does not need to install or configure a database. The
  command is just `pip install -r requirements-dev.txt` then `pytest`.
- Before each test the `orders` table is reset to three known rows, so tests do not
  depend on each other.
- On Windows (where `pgserver` is not available) you set `TEST_DATABASE_URL` to any
  Postgres, for example the one from `docker compose up db`, and the tests use that.

---

## What to say if asked "why so few tests?"

Be direct and confident: "I tested the parts that carry risk or prove the design,
not everything for the sake of a coverage number. The hub has the fan-out and
slow-client logic, so it gets unit tests. The real-time path is the point of the
project, so the two guarantees, snapshot-on-connect and database-driven delivery,
are asserted end to end against a real Postgres. The REST layer gets basic checks.
With more time I would add tests for reconnection and for de-duplication on the
client."

That answer shows judgment about *what* to test, which matters more than the count.

---

## What is deliberately not tested (say so before they find it)

- The browser client's JavaScript (de-duplication, reconnect handling) is not
  covered by automated tests; it was checked by hand. Testing it would mean adding
  a browser test runner, which is more machinery than this scope needs.
- No load or performance tests. Those numbers depend on real hardware, so guessing
  them would be dishonest (see `05-candidate-briefing.md` on talking about scale).
