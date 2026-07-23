# API reference

Base URL in local development: `http://localhost:8000`.

## The stream

### `GET /events`

A Server-Sent Events stream. This is the real-time channel.

On connect, the server sends a `snapshot` event with all current orders, then a
`data` event for each change after that. A comment line (`: ping`) is sent
periodically as a heartbeat to keep the connection open.

Snapshot event:

```
event: snapshot
data: [{"id":1,"customer_name":"Ada Lovelace","product_name":"Mechanical Keyboard","status":"pending","updated_at":"2026-01-01T10:00:00+00:00"}, ...]
```

Change events:

```
data: {"operation":"INSERT","id":4,"data":{"id":4,"customer_name":"Zoe","product_name":"Cable","status":"pending","updated_at":"..."}}

data: {"operation":"UPDATE","id":4,"data":{"id":4,...,"status":"shipped",...}}

data: {"operation":"DELETE","id":4,"data":{"id":4,...}}
```

`operation` is one of `INSERT`, `UPDATE`, or `DELETE`. For a delete, treat the row
as gone; the `data` reflects the row as it was.

A client consumes this by loading the snapshot as its starting state, then applying
each change. Apply a change only if its `updated_at` is the same or newer than the
version already held, which handles duplicates and out-of-order messages.

## Orders

### `GET /orders`

Returns all orders as a JSON array. Useful for simple clients that do not consume
the stream.

### `POST /orders`

Create an order. Body:

```json
{ "customer_name": "Ada", "product_name": "Keyboard", "status": "pending" }
```

`status` is optional and defaults to `pending`. It must be one of `pending`,
`shipped`, or `delivered`. Returns the created order with status `201`.

### `PATCH /orders/{id}`

Update an order. Any subset of fields may be sent:

```json
{ "status": "shipped" }
```

Returns the updated order, or `404` if the id does not exist.

### `DELETE /orders/{id}`

Delete an order. Returns `204` on success, or `404` if the id does not exist.

## Health

### `GET /health`

Returns basic liveness information and the current number of connected stream
clients:

```json
{ "status": "ok", "subscribers": 3 }
```

## A note on writes and the stream

Any write through these endpoints causes the database trigger to fire, which
produces a change event on `GET /events`. The dashboard relies on this: when you
create, edit, or delete an order in the UI, the change you see on screen is the one
that came back over the stream, not a local guess. That is a deliberate way to show
the real-time path actually works.
