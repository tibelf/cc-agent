# Repository Guidelines

## Project Structure & Module Organization
- Root Python app; key entry points: `auto_claude.py` (run loop) and `taskctl.py` (CLI).
- Core modules: `worker.py`, `task_manager.py`, `rate_limit_manager.py`, `recovery_manager.py`, `security.py`, `monitoring.py`, `database.py`, `models.py`, `utils.py`.
- Support dirs: `config/` (runtime settings), `tasks/` (task data), `logs/`, `db/` (SQLite), `queue/`, `snapshots/`, `auth/`.
- Tests live in `tests/` and should mirror module structure (e.g., `tests/test_worker.py`).

## Build, Test, and Development Commands
- `make install` — Install deps and run `taskctl.py init`.
- `make dev-setup` — Editable install + dev tools (pytest, black, flake8).
- `make run` — Start the system locally (`python auto_claude.py`).
- `make test` — Run tests via pytest (`python -m pytest -v`).
- `make format` / `make lint` — Format with Black; lint with Flake8.
- `make clean` — Remove build artifacts and caches.
- Service ops (require sudo): `make setup-service`, `make start`, `make stop`, `make status`, `make logs`.

## Coding Style & Naming Conventions
- Python 3.8+; 4‑space indentation; prefer type hints.
- Formatting: Black (project‑wide). Lint: Flake8 with `--max-line-length=100 --ignore=E203,W503`.
- Naming: modules/files `snake_case`; functions/vars `snake_case`; classes `PascalCase`; constants `UPPER_SNAKE_CASE`.
- Keep functions cohesive; prefer small utilities in `utils.py`. Add docstrings for public functions/classes.

## Testing Guidelines
- Framework: pytest. Name tests `tests/test_*.py`; group per module.
- Cover critical flows in `task_manager.py`, `worker.py`, `security.py`, and error/limit handling.
- Use `tmp_path` for filesystem writes; avoid touching `tasks/` and `db/` in unit tests.
- Run locally with `make test` (or `python -m pytest tests/ -v`). Add tests with new features/bug fixes.

## Commit & Pull Request Guidelines
- Commits: imperative, concise subject (optionally Conventional Commits), e.g., `fix(worker): prevent hang on rate limit`.
- PRs: clear description, linked issue, reproduction (for bugs), before/after notes, and updated tests/docs when applicable.
- Pre‑push: run `make format`, `make lint`, and `make test` and ensure they pass.

## Security & Configuration Tips
- Never commit secrets. Configuration lives in `config/`; review `config/config.py` defaults.
- The security layer blocks risky commands; do not bypass checks. Keep logs free of sensitive data.
- Prefer explicit error handling and structured logging (see `monitoring.py`).

## Agent‑Specific Instructions
- Keep changes minimal and focused; follow this file’s scope across the repo.
- Do not modify unrelated files; prefer localized patches and align tests.
- Use Makefile targets for validation; avoid introducing new tooling without discussion.
