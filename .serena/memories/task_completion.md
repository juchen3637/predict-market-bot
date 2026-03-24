# Task Completion Checklist

After completing any coding task in this project:

## 1. Code Quality
- [ ] Functions are small (<50 lines)
- [ ] Files are focused (<800 lines); skills scripts are typically 100-300 lines
- [ ] No hardcoded secrets or API keys
- [ ] All external inputs validated (API responses, config values)
- [ ] Immutable patterns used — return new objects, don't mutate in-place
- [ ] Error handling is explicit and logged

## 2. Testing
```bash
pytest tests/                   # All tests must pass
pytest tests/test_<module>.py   # Run module-specific tests
```
- Tests live in `tests/` with 1:1 mapping to skill module files
- Use `pytest`, `MagicMock`, `tmp_path` for isolation
- TDD: write test first (RED), implement (GREEN), refactor
- Target 80%+ coverage

## 3. No Linter Configured
- No linting step currently configured
- Follow PEP 8 manually; use `from __future__ import annotations`

## 4. Config Changes
- If adding new parameters, add them to `config/settings.yaml`
- Never put secrets in `settings.yaml`; use `.env` / environment variables

## 5. Pipeline Safety
- If modifying pipeline stages, check `data/pipeline_state.json` reset behavior
- Respect the `STOP` kill switch file check in `run_pipeline.py`
- Verify `PAPER_TRADING=true` before any live execution changes
