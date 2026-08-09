"""
fetch_data.py

Pulls historical time-series data for each monitored metric from Prometheus
and saves it locally as CSV, ready for forecast.py to train on.

Run this before forecast.py. In production this is triggered hourly via cron
alongside forecast.py (see README.md for the crontab entry).
"""

import requests
import pandas as pd
import time

PROM_URL = "http://localhost:9090"
END = int(time.time())
START = END - 7 * 24 * 3600  # last 7 days of history; adjust down if less is available

# Each entry: (PromQL query, step in seconds)
# Note: CPU uses a finer step (60s) than its 5m rate() window to avoid
# sampling artifacts that make the rate look artificially flat.
METRICS = {
    "disk_e": (
        '100 - ((windows_logical_disk_free_bytes{volume="E:"} '
        '/ windows_logical_disk_size_bytes{volume="E:"}) * 100)',
        "300",
    ),
    "disk_f": (
        '100 - ((windows_logical_disk_free_bytes{volume="F:"} '
        '/ windows_logical_disk_size_bytes{volume="F:"}) * 100)',
        "300",
    ),
    "cpu": (
        '100 - (avg by(instance)(rate(windows_cpu_time_total{mode="idle"}[5m])) * 100)',
        "60",
    ),
    "memory": (
        '100 - ((windows_memory_available_bytes '
        '/ windows_memory_physical_total_bytes) * 100)',
        "300",
    ),
}


def fetch_metric(name: str, query: str, step: str) -> None:
    """Fetch one metric's history from Prometheus and write it to history_<name>.csv."""
    resp = requests.get(
        f"{PROM_URL}/api/v1/query_range",
        params={"query": query, "start": START, "end": END, "step": step},
    )

    if resp.status_code != 200:
        print(f"[{name}] Prometheus query failed: HTTP {resp.status_code}")
        return

    result = resp.json().get("data", {}).get("result", [])

    if not result:
        print(f"[{name}] No data returned — check the query/labels in Prometheus UI")
        return

    df = pd.DataFrame(result[0]["values"], columns=["ds", "y"])
    df["ds"] = pd.to_datetime(df["ds"], unit="s")
    df["y"] = df["y"].astype(float)
    df.to_csv(f"history_{name}.csv", index=False)
    print(f"[{name}] Saved {len(df)} rows -> history_{name}.csv")


def main() -> None:
    for name, (query, step) in METRICS.items():
        fetch_metric(name, query, step)


if __name__ == "__main__":
    main()
