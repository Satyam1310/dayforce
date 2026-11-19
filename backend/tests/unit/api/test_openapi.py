"""docs/openapi.yaml must match the running app, so the frontend and QA code against the truth."""

import yaml

from scripts.export_openapi import OPENAPI_PATH, render


def test_openapi_yaml_is_up_to_date():
    assert OPENAPI_PATH.exists(), "run: cd backend && python -m scripts.export_openapi"
    assert OPENAPI_PATH.read_text() == render(), (
        "docs/openapi.yaml is out of date. Run: cd backend && python -m scripts.export_openapi"
    )


def test_openapi_lists_the_cases_endpoints():
    paths = set(yaml.safe_load(render())["paths"])
    assert {
        "/api/cases",
        "/api/cases/{case_id}",
        "/api/cases/{case_id}/propose",
        "/api/cases/{case_id}/approve",
        "/api/cases/{case_id}/reject",
        "/health",
    } <= paths
