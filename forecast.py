"""
forecast.py

Trains a Prophet time-series model per metric on the history CSVs produced
by fetch_data.py, generates a 72-hour forecast, and pushes the 6h/24h/72h
forecast values to Prometheus Pushgateway as gauges (e.g. cpu_forecast_pct).

Model notes:
- Uses linear growth (Prophet's default) rather than logistic growth.
  Logistic growth (cap/floor bounded) was tried first since all metrics
  here are percentages, but on a short/noisy history it collapsed
  predictions toward the floor instead of producing a sensible trend.
- Output is clipped to [0, 100] after prediction instead, which gives a
  physically valid forecast without destabilizing the model fit.
"""

import pandas as pd
from prophet import Prophet
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

PUSHGATEWAY_URL = "localhost:9091"
JOB_NAME = "metric_forecaster"

METRICS = ["disk_e", "disk_f", "cpu", "memory"]

# Horizon label -> number of future hourly steps ahead to read the forecast from
HORIZONS = {"6h": 6, "24h": 24, "72h": 72}

FORECAST_PERIODS = 72  # hours ahead to forecast
FORECAST_FREQ = "h"

# All metrics here are bounded percentages (0-100); forecasts are clipped
# to this range after prediction.
CAP = 100
FLOOR = 0


def forecast_metric(name: str):
    """Train a Prophet model for one metric and return its future forecast rows."""
    try:
        df = pd.read_csv(f"history_{name}.csv", parse_dates=["ds"])
    except FileNotFoundError:
        print(f"[{name}] No history file found, skipping (run fetch_data.py first)")
        return None

    if len(df) < 10:
        print(f"[{name}] Not enough data points yet ({len(df)}), skipping")
        return None

    model = Prophet(
        growth="linear",
        daily_seasonality=False,
        weekly_seasonality=False,
    )
    model.fit(df)

    future = model.make_future_dataframe(periods=FORECAST_PERIODS, freq=FORECAST_FREQ)
    forecast = model.predict(future)

    # Clip to physically valid percentage bounds instead of letting
    # linear extrapolation run past what's physically possible.
    forecast["yhat"] = forecast["yhat"].clip(lower=FLOOR, upper=CAP)
    forecast["yhat_lower"] = forecast["yhat_lower"].clip(lower=FLOOR, upper=CAP)
    forecast["yhat_upper"] = forecast["yhat_upper"].clip(lower=FLOOR, upper=CAP)

    future_only = forecast[forecast["ds"] > df["ds"].max()][
        ["ds", "yhat", "yhat_lower", "yhat_upper"]
    ]
    future_only.to_csv(f"forecast_{name}.csv", index=False)

    print(f"[{name}] Forecast complete, {len(future_only)} future points generated")
    return future_only


def main() -> None:
    registry = CollectorRegistry()
    gauges = {
        name: Gauge(
            f"{name}_forecast_pct",
            f"Forecasted {name} usage/value %",
            ["horizon"],
            registry=registry,
        )
        for name in METRICS
    }

    any_pushed = False

    for name in METRICS:
        future_only = forecast_metric(name)
        if future_only is None or future_only.empty:
            continue

        for label, h in HORIZONS.items():
            idx = min(h - 1, len(future_only) - 1)
            row = future_only.iloc[idx]
            gauges[name].labels(horizon=label).set(row["yhat"])

        any_pushed = True

    if any_pushed:
        push_to_gateway(PUSHGATEWAY_URL, job=JOB_NAME, registry=registry)
        print("Pushed all forecasts to Pushgateway")
    else:
        print("Nothing to push — no valid forecasts generated")


if __name__ == "__main__":
    main()
