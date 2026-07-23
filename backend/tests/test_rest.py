"""Basic checks on the REST endpoints."""


async def test_list_returns_seeded_orders(client):
    res = await client.get("/orders")
    assert res.status_code == 200
    orders = res.json()
    assert len(orders) == 3
    assert orders[0]["customer_name"] == "Ada Lovelace"


async def test_create_order(client):
    res = await client.post(
        "/orders",
        json={"customer_name": "Zoe", "product_name": "Cable", "status": "pending"},
    )
    assert res.status_code == 201
    created = res.json()
    assert created["id"] == 4
    assert created["status"] == "pending"


async def test_update_order_changes_status(client):
    res = await client.patch("/orders/1", json={"status": "shipped"})
    assert res.status_code == 200
    assert res.json()["status"] == "shipped"


async def test_invalid_status_is_rejected(client):
    res = await client.post(
        "/orders",
        json={"customer_name": "X", "product_name": "Y", "status": "not-a-status"},
    )
    assert res.status_code == 422  # validation error


async def test_delete_missing_order_returns_404(client):
    res = await client.delete("/orders/9999")
    assert res.status_code == 404
