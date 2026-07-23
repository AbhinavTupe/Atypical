#!/usr/bin/env python3
"""Minimal CLI client for the real-time orders stream.

Shows that the stream is not tied to any one UI: no framework, just a plain HTTP
request that reads the SSE stream line by line. Uses only the Python standard
library, so there is nothing to install.

The stream sends a `snapshot` event first (all current orders), then a `data`
event for each later change. This client prints the snapshot as a starting table,
then prints each change as it arrives.

Usage:
    python cli.py                       # connect to http://localhost:8000
    python cli.py http://host:8000      # custom base URL
"""
import json
import sys
import urllib.request

RESET, DIM, GREEN, YELLOW, RED = "\033[0m", "\033[2m", "\033[32m", "\033[33m", "\033[31m"
COLOR = {"INSERT": GREEN, "UPDATE": YELLOW, "DELETE": RED}


def print_row(op: str, row: dict) -> None:
    color = COLOR.get(op, RESET)
    print(f"{color}{op:<6}{RESET} #{str(row.get('id', '?')):<3} "
          f"{str(row.get('customer_name', '-')):<16} "
          f"{str(row.get('product_name', '-')):<20} "
          f"{DIM}{row.get('status', '')}{RESET}")


def stream(base_url: str) -> None:
    url = f"{base_url.rstrip('/')}/events"
    print(f"{DIM}connecting to {url} ...{RESET}")
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})

    event_name = "message"
    with urllib.request.urlopen(req) as resp:
        print(f"{GREEN}connected. waiting for changes (Ctrl-C to quit){RESET}\n")
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\n")
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
                continue
            if not line.startswith("data:"):
                continue  # heartbeats / comments / blank separators
            data = line[len("data:"):].strip()
            if not data:
                continue

            if event_name == "snapshot":
                rows = json.loads(data)
                print(f"{DIM}current state ({len(rows)} orders):{RESET}")
                for row in rows:
                    print_row("--", row)
                print()
                event_name = "message"
            else:
                evt = json.loads(data)
                row = evt.get("data") or {"id": evt["id"]}
                print_row(evt["operation"], row)


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    try:
        stream(base)
    except KeyboardInterrupt:
        print(f"\n{DIM}bye{RESET}")
