#!/usr/bin/env python3
"""Load test for the API. Only for an API that runs the MOCK LLM.

Each "flow" is what a manager's day looks like, compressed: send a timecard with a missed
punch (POST /api/analysis/run), ask for a proposal (POST /api/cases/{id}/propose), approve it
or reject it, and read the case list. Many flows run at once. It prints latency percentiles
per endpoint and the error rate.

    # terminal 1 (mock LLM, a database with pgvector):
    cd backend && LLM_PROVIDER=mock DATABASE_URL=postgresql://... uvicorn app.main:app --port 8000
    # terminal 2:
    python scripts/load_test.py --base-url http://127.0.0.1:8000 --flows 100 --concurrency 10

Safety: this script never calls a model itself and never needs a key. It refuses any address
that is not local (use --allow-remote for a test server of your own), refuses anything with
"groq" in the host name, and stops at once if a proposal looks like it came from a real model
(the mock always answers "Applying POL-TA-xx to resolve the TYPE exception.").
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from ipaddress import ip_address

MOCK_RATIONALE = re.compile(r"^Applying POL-TA-\d+ to resolve the [A-Z_]+ exception\.$")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}  # noqa: S104 (a name, not a bind)


class NotMock(Exception):
    """A proposal did not look like the mock LLM's answer."""


def check_target(base_url: str, allow_remote: bool) -> None:
    host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    if "groq" in host:
        sys.exit("refused: never point the load test at Groq")
    if host in LOCAL_HOSTS or host.endswith(".localhost"):
        return
    try:
        if ip_address(host).is_private or ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    if not allow_remote:
        sys.exit(
            f"refused: {host!r} is not a local address "
            "(add --allow-remote if it is your own test server)"
        )


def missed_punch_timecard() -> dict:
    emp = f"load-{uuid.uuid4().hex[:8]}"
    tz = "+05:30"

    def at(h: int, m: int = 0) -> str:
        return f"2026-03-09T{h:02d}:{m:02d}:00{tz}"

    return {
        "id": str(uuid.uuid4()),
        "employee_id": emp,
        "work_date": "2026-03-09",
        "punches": [
            {"id": str(uuid.uuid4()), "employee_id": emp, "type": "IN", "timestamp": at(9)}
        ],
        "shifts": [
            {
                "id": str(uuid.uuid4()),
                "employee_id": emp,
                "start": at(9),
                "end": at(17, 30),
                "location_id": "loc-load",
            }
        ],
        "notes": None,
    }


class Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.latencies: dict[str, list[float]] = defaultdict(list)
        self.errors: dict[str, int] = defaultdict(int)
        self.error_samples: list[str] = []

    def add(self, name: str, seconds: float, ok: bool, note: str = "") -> None:
        with self.lock:
            self.latencies[name].append(seconds)
            if not ok:
                self.errors[name] += 1
                if len(self.error_samples) < 5:
                    self.error_samples.append(f"{name}: {note}")


def call(rec: Recorder, name: str, base: str, method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read() or b"null")
            rec.add(name, time.perf_counter() - start, True)
            return payload
    except urllib.error.HTTPError as e:
        rec.add(name, time.perf_counter() - start, False, f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        rec.add(name, time.perf_counter() - start, False, type(e).__name__)
    return None


def one_flow(rec: Recorder, base: str, index: int, stop: threading.Event) -> None:
    if stop.is_set():
        return
    result = call(
        rec,
        "POST /api/analysis/run",
        base,
        "POST",
        "/api/analysis/run",
        {"timecards": [missed_punch_timecard()]},
    )
    if not result or not result["cases"]:
        return
    case_id = result["cases"][0]["exception"]["id"]
    proposal = call(
        rec, "POST /api/cases/{id}/propose", base, "POST", f"/api/cases/{case_id}/propose"
    )
    if not proposal:
        return
    if proposal["source"] == "LLM" and not MOCK_RATIONALE.match(proposal["rationale"]):
        stop.set()
        raise NotMock("a proposal did not look like the mock LLM's: stopping")
    manager = {"manager_id": "mgr-load"}
    if index % 4 == 3:
        call(
            rec,
            "POST /api/cases/{id}/reject",
            base,
            "POST",
            f"/api/cases/{case_id}/reject",
            {**manager, "comment": "load test reject"},
        )
    else:
        call(
            rec,
            "POST /api/cases/{id}/approve",
            base,
            "POST",
            f"/api/cases/{case_id}/approve",
            manager,
        )
    call(rec, "GET /api/cases/{id}", base, "GET", f"/api/cases/{case_id}")
    call(rec, "GET /api/cases?status=OPEN", base, "GET", "/api/cases?status=OPEN")
    call(rec, "GET /health", base, "GET", "/health")


def percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    if not s:
        return 0.0
    k = max(0, min(len(s) - 1, round(p / 100 * len(s) + 0.5) - 1))
    return s[k]


def report(rec: Recorder, elapsed: float) -> tuple[str, float]:
    rows = [
        f"{'endpoint':<34}{'n':>6}{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}{'max ms':>9}{'errors':>8}"
    ]
    total = errors = 0
    for name in sorted(rec.latencies):
        v = rec.latencies[name]
        total += len(v)
        errors += rec.errors[name]
        rows.append(
            f"{name:<34}{len(v):>6}{percentile(v, 50) * 1000:>9.1f}{percentile(v, 95) * 1000:>9.1f}"
            f"{percentile(v, 99) * 1000:>9.1f}{max(v) * 1000:>9.1f}{rec.errors[name]:>8}"
        )
    everything = [x for v in rec.latencies.values() for x in v]
    rate = errors / total if total else 1.0
    rows.append("")
    rows.append(
        f"requests {total}  errors {errors} ({rate:.2%})  duration {elapsed:.1f}s  "
        f"throughput {total / elapsed:.1f} req/s"
    )
    if everything:
        rows.append(
            f"all requests: p50 {statistics.median(everything) * 1000:.1f} ms  "
            f"p95 {percentile(everything, 95) * 1000:.1f} ms  "
            f"p99 {percentile(everything, 99) * 1000:.1f} ms"
        )
    for sample in rec.error_samples:
        rows.append(f"  error sample: {sample}")
    return "\n".join(rows), rate


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--flows", type=int, default=50, help="how many flows to run (default 50)")
    ap.add_argument("--concurrency", type=int, default=5, help="flows at the same time (default 5)")
    ap.add_argument("--allow-remote", action="store_true", help="allow a non-local address")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    check_target(base, args.allow_remote)
    if args.flows < 1 or args.concurrency < 1:
        sys.exit("--flows and --concurrency must be at least 1")

    rec, stop = Recorder(), threading.Event()
    health = call(rec, "GET /health", base, "GET", "/health")
    if not health:
        sys.exit(f"the API at {base} did not answer /health")
    rec.latencies.clear()
    rec.errors.clear()

    print(f"{args.flows} flows, {args.concurrency} at a time, against {base} (mock LLM only)")
    start = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            for future in [pool.submit(one_flow, rec, base, i, stop) for i in range(args.flows)]:
                future.result()
    except NotMock as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        return 2
    text, rate = report(rec, time.perf_counter() - start)
    print(text)
    return 0 if rate == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
