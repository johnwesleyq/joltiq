"""Minimal Prometheus exposition (no extra dependency): request counts/latency + custom counters."""
from __future__ import annotations

import threading
from collections import defaultdict

_lock = threading.Lock()
_req_count: dict[tuple[str, str, int], int] = defaultdict(int)
_req_ms_sum: dict[tuple[str, str], float] = defaultdict(float)
_req_ms_count: dict[tuple[str, str], int] = defaultdict(int)
_counters: dict[tuple[str, tuple], float] = defaultdict(float)
BUCKETS = [5, 25, 100, 250, 500, 1000, 2500, 10000]
_hist: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0] * (len(BUCKETS) + 1))


def observe_request(method: str, route: str, status: int, ms: float) -> None:
    with _lock:
        _req_count[(method, route, status)] += 1
        _req_ms_sum[(method, route)] += ms
        _req_ms_count[(method, route)] += 1
        h = _hist[(method, route)]
        for i, b in enumerate(BUCKETS):
            if ms <= b:
                h[i] += 1
        h[-1] += 1


def inc(name: str, value: float = 1, **labels: str) -> None:
    with _lock:
        _counters[(name, tuple(sorted(labels.items())))] += value


def render() -> str:
    lines = ["# TYPE joltiq_http_requests_total counter"]
    with _lock:
        for (m, r, s), n in _req_count.items():
            lines.append(f'joltiq_http_requests_total{{method="{m}",route="{r}",status="{s}"}} {n}')
        lines.append("# TYPE joltiq_http_request_duration_ms histogram")
        for (m, r), h in _hist.items():
            for i, b in enumerate(BUCKETS):
                lines.append(f'joltiq_http_request_duration_ms_bucket{{method="{m}",route="{r}",le="{b}"}} {h[i]}')
            lines.append(f'joltiq_http_request_duration_ms_bucket{{method="{m}",route="{r}",le="+Inf"}} {h[-1]}')
            lines.append(f'joltiq_http_request_duration_ms_sum{{method="{m}",route="{r}"}} {_req_ms_sum[(m, r)]:.1f}')
            lines.append(f'joltiq_http_request_duration_ms_count{{method="{m}",route="{r}"}} {_req_ms_count[(m, r)]}')
        for (name, labels), v in _counters.items():
            lab = ",".join(f'{k}="{val}"' for k, val in labels)
            lines.append(f"joltiq_{name}{{{lab}}} {v}")
    return "\n".join(lines) + "\n"
