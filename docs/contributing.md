# Contributing

Keep each change small, tested, documented, and safe for applications that do
not enable the optional database or cache integrations.

## Quick path

```console
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Run the supported Django matrix before proposing a release:

```console
uv run python -m tools.test_matrix --django-version 5.2.17
uv run python -m tools.test_matrix --django-version 6.1.1
```

## Strict TDD

1. Add a focused test and confirm it fails for the intended reason.
2. Implement the smallest change that makes it pass.
3. Refactor while keeping the test green.
4. Run the relevant regression suite.

Total and branch coverage must remain at or above 95%. Tests belong in the same
work unit as the behavior they protect.

## Python contract

Public modules, classes, functions, and methods use complete Google-style
docstrings. Include `Args`, `Returns`, and `Raises` only when they apply. Parameter
and return type hints are mandatory; do not repeat types in
docstrings. Do not place backticks inside Python docstrings.

## Documentation contract

- Lead with the supported outcome, then disclose configuration and edge cases.
- Keep consumer-owned application screens outside package source.
- Add every public page to `zensical.yml` and keep local links portable.
- Update tests whenever navigation, commands, settings, or public imports change.
- Keep private planning notes outside the published documentation; public guides
  describe only the current supported behavior.

Use Conventional Commits and never include generated-author attribution. See
[Release and rollback](release-rollback.md) before changing publication code.
