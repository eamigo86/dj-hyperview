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

Line coverage and branch coverage must each remain at or above 95%. The canonical
runner checks the exact XML counters independently after combining the base and
Admin profiles; a passing combined percentage cannot compensate for either
shortfall. Tests belong in the same work unit as the behavior they protect.

## Python contract

Three rules cover every Python docstring.

A. **Complete Google style for everything public.** Every public module, class,
function and method has a complete Google-style docstring with exact, non-empty
sections. Public means:

- an importable module;
- a name without a leading underscore, or a name listed in `__all__`;
- a class member that is not private.

`Args:` also documents `*args` and `**kwargs`. Include `Args:`, `Returns:`,
`Yields:` and `Raises:` only when they apply.

B. **Types live in the signature.** Parameters and return values carry type hints.
`Args:`, `Returns:`/`Yields:` and `Raises:` describe behavior and do not repeat the
types.

C. **Plain text.** No docstring contains backticks, including the docstrings of
private, nested and dunder functions.

Tools enforce only part of that contract.

- `ruff check` (D1) rejects a public module, class, function or method without a
  docstring, dunder methods included (D105). Ruff treats a name or a module that
  starts with an underscore as private and skips it, and it does not read `__all__`,
  so it never asks for the module docstring of an underscore module or for the
  docstring of an underscore name that `__all__` exports. The test below covers
  exported names.
- `ruff check` (D417, Google convention) rejects a parameter, `*args` and
  `**kwargs` included, missing from an `Args` section, and so catches an empty
  `Args` section on a function that has parameters. It checks only a docstring that
  already has an `Args` section, so it never asks for one.
- `ruff check` (ANN) rejects a function in `src/`, public or private, whose
  parameters, `*args`, `**kwargs` or return are not annotated. `Any` is allowed,
  because ANN401 is ignored.
- `tests/test_public_api_quality.py` rejects backticks in the docstring of every
  module, class and function under `src/dj_hyperview`, nested and private ones
  included, and requires a docstring on every exported name and on every public
  member it inherits, including those defined in underscore modules.

No tool checks these, so review checks them by hand:

- `Yields:` on a generator, `Returns:` and `Raises:` completeness, and an `Args:`
  section where parameters call for one;
- exact section headings, and sections that are empty or misspelled;
- types repeated inside a docstring;
- the module docstring of an underscore module;
- the quality of every description.

Tests and tools are exempt from the docstring and annotation rules, and generated
migrations from the docstring rules, as the Ruff configuration in `pyproject.toml`
exempts them. Rule C still applies to every docstring in `tests/` and `tools/`
that exists, although no tool checks it there.

## Documentation contract

- Lead with the supported outcome, then disclose configuration and edge cases.
- Keep consumer-owned application screens outside package source.
- Add every public page to `zensical.yml` and keep local links portable.
- Update tests whenever navigation, commands, settings, or public imports change.
- Keep private planning notes outside the published documentation. Canonical guides
  describe current behavior; the single [roadmap](roadmap.md) labels future work.
- Apply the [beta compatibility policy](api-reference.md#beta-compatibility-policy)
  to public contract changes; document migrations and retain regression coverage.

Use Conventional Commits and never include generated-author attribution. See
[Release and rollback](release-rollback.md) before changing publication code.
