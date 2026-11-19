"""Prometheus metrics and the request middleware (request ID + timing + access log)."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from .logging import request_id_var

log = logging.getLogger("app.access")

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["method", "route"]
)
PROPOSALS = Counter(
    "proposals_created_total", "Proposals created by the AI workflow", ["source", "status"]
)
DECISIONS = Counter("decisions_total", "Manager decisions", ["decision"])
# AI safety signals. Proposals by source (LLM or RULES_FALLBACK) are PROPOSALS above.
VALIDATOR_REJECTIONS = Counter(
    "validator_rejections_total", "Fixes the validator rejected (LLM fix or rule-based fix)"
)
ESCALATIONS = Counter(
    "escalations_total", "Cases escalated to a manager with no AI fix", ["reason"]
)
LLM_ERRORS = Counter(
    "llm_errors_total", "Failed LLM calls: timeout, provider_429, local_rate_limit, other", ["kind"]
)
LLM_LATENCY = Histogram(
    "llm_request_duration_seconds",
    "LLM call latency (includes any wait for the rate limit)",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30),
)
ANALYSIS_RUNS = Counter("analysis_runs_total", "Calls to POST /api/analysis/run")
EXCEPTIONS_DETECTED = Counter(
    "exceptions_detected_total", "Exceptions found by the rule detectors", ["type"]
)


def install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = rid
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = getattr(request.scope.get("route"), "path", "unmatched")
            HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
            HTTP_LATENCY.labels(request.method, route).observe(elapsed)
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": round(elapsed * 1000, 1),
                },
            )
            request_id_var.reset(token)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
