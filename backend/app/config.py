"""Runtime configuration, read from environment variables."""
import os

# Database connection URL (asyncpg DSN).
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/orders_db",
)

# Postgres channel used by the trigger's pg_notify() call.
NOTIFY_CHANNEL: str = os.getenv("NOTIFY_CHANNEL", "orders_changes")

# Seconds between SSE heartbeat comments (keeps proxies/browsers from timing out).
HEARTBEAT_SECONDS: float = float(os.getenv("HEARTBEAT_SECONDS", "15"))
