# Private Notes - Start Here

> These notes are for you only. They are **not** referenced anywhere in the
> project's public files (README, code, etc.), and nothing in the repo points to
> this folder. Keep it that way.

Read them in this order:

1. **`01-project-explained.md`** - The whole system in plain language, assuming you
   only know Python. Read this first, top to bottom. If you understand only one
   file, make it this one.

2. **`02-concepts-explained.md`** - A dictionary of every technical term used
   anywhere in the project (database, trigger, LISTEN/NOTIFY, async, SSE, Docker,
   queue, fan-out, and so on). Use it as a lookup whenever a word is unclear.

3. **`04-tradeoffs.md`** - Why each design decision was made, what it costs, and
   when you'd choose differently. This is what impresses interviewers: not just
   *what* you built, but *why*.

4. **`03-interview-qna.md`** - Every question an interviewer is likely to ask about
   the implementation, with an answer you can say in your own words. Practise these
   out loud. Includes the Redis vs pg_notify deep-dive, which is the most likely
   follow-up.

5. **`05-candidate-briefing.md`** - The strategy around the questions: the
   one-minute pitch, why each choice in a line, what to volunteer, what to say and
   what to avoid, and how to talk about scale without inventing numbers. Read this
   last, right before you send the project or walk into the conversation.

6. **`06-testing.md`** - What the test suite covers, how it runs, and how to answer
   "why so few tests" with confidence.

## The 30-second version, so you're never caught flat

The task: when data changes in the database, connected clients must find out
instantly, without polling.

The idea: don't push updates from our own code (that only catches changes we make).
Instead, a **trigger inside Postgres** fires on *every* insert/update/delete and
broadcasts it using Postgres's built-in **LISTEN/NOTIFY** messaging. Our Python
server listens for those broadcasts and relays each one to all connected clients
over **Server-Sent Events (SSE)**. Because the database itself is the source of the
notifications, it works no matter who changed the data.

The proof it's right: we changed a row *directly in the database*, bypassing the
API completely, and every client still updated.
