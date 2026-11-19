"""Export the API description to docs/openapi.yaml (CLAUDE.md: checked in, shared by everyone).

Run after changing any endpoint:   cd backend && python -m scripts.export_openapi
A unit test (tests/unit/api/test_openapi.py) fails if the file is out of date.
"""

from __future__ import annotations

from pathlib import Path

import yaml

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "docs" / "openapi.yaml"


def render() -> str:
    from app.main import create_app

    spec = create_app().openapi()
    return yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100)


def main() -> int:
    OPENAPI_PATH.parent.mkdir(parents=True, exist_ok=True)
    OPENAPI_PATH.write_text(render())
    print(f"wrote {OPENAPI_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
