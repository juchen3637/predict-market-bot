# Code Style & Conventions

## General
- Python 3.x with `from __future__ import annotations`
- Module-level docstring in triple quotes: `"""module_name.py — Short description\n\nLonger description...\n"""`
- Type hints used throughout (dataclasses, function signatures)
- `dataclass` for structured data objects (e.g., `PositionSize`, `PipelineResult`)
- `pathlib.Path` preferred over string paths
- `pydantic v2` for external data validation at system boundaries
- `PyYAML` for config loading (`config/settings.yaml`)
- `python-dotenv` for secrets (never hardcoded)

## Naming
- `snake_case` for functions and variables
- `PascalCase` for classes and dataclasses
- `UPPER_SNAKE_CASE` for module-level constants
- Private helpers prefixed with `_` (e.g., `_run_stage`, `_load_state`)

## File Organization
- Skills organized under `skills/<skill-name>/scripts/` — each script is focused
- Tests in `tests/` with 1:1 mapping to skill modules
- `tests/conftest.py` adds all script dirs to `sys.path` for direct imports
- Section separators use `# ---...--- #` style comment blocks with labels

## Error Handling
- Explicit error handling at every level
- Server-side: detailed logging with context
- No silent swallowing of errors
- Validate all external data at system boundaries (API responses, user input)

## Immutability
- Create new objects rather than mutating existing ones
- Dataclasses used for value objects

## Configuration
- All tunable parameters in `config/settings.yaml` (no secrets)
- API keys and secrets in `.env` file (never committed)
- Config loaded via `config_loader.load_settings()` from pm-risk
- `PAPER_TRADING` env var controls paper vs live execution

## Imports
- Standard library first, then third-party, then local
- Local skill imports use direct module name (relies on conftest.py sys.path setup)
- `# noqa: E402` used for path-dependent imports
