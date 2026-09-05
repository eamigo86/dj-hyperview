"""Reusable fail-closed workflow contract validation for tests."""

import yaml


def load_workflow(text: str) -> object:
    """Load workflow YAML while retaining the literal on key.

    Args:
        text: Workflow YAML source.

    Returns:
        The parsed YAML value.

    Raises:
        yaml.YAMLError: The source is not valid YAML.
    """
    return yaml.load(text, Loader=yaml.BaseLoader)


def _normalize_run(script: object) -> str | None:
    """Normalize blank and full-line comment formatting in run scripts."""
    if not isinstance(script, str):
        return None
    return "\n".join(
        stripped
        for line in script.splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    )


def _semantic(value: object) -> object:
    """Normalize harmless run formatting while preserving YAML values."""
    if isinstance(value, dict):
        return {
            key: _normalize_run(item) if key == "run" else _semantic(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_semantic(item) for item in value]
    return value


def _node_signature(
    node: yaml.Node, *, normalize_run: bool = False
) -> tuple[object, ...]:
    """Represent a YAML node with sequence order and scalar tags."""
    if isinstance(node, yaml.ScalarNode):
        value = _normalize_run(node.value) if normalize_run else node.value
        return ("scalar", node.tag, value)
    if isinstance(node, yaml.SequenceNode):
        return (
            "sequence",
            node.tag,
            tuple(_node_signature(item) for item in node.value),
        )
    if isinstance(node, yaml.MappingNode):
        pairs = (
            (
                _node_signature(key),
                _node_signature(
                    value,
                    normalize_run=isinstance(key, yaml.ScalarNode)
                    and key.value == "run",
                ),
            )
            for key, value in node.value
        )
        return ("mapping", node.tag, tuple(sorted(pairs, key=repr)))
    raise TypeError("unsupported YAML node")


def _tagged_signature(text: str) -> tuple[object, ...]:
    """Compose source into a canonical signature retaining YAML tags."""
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    if node is None:
        raise TypeError("missing YAML node")
    return _node_signature(node)


def audit_workflow(candidate: str, contract: str, *, label: str) -> list[str]:
    """Return stable violations against a readable workflow contract.

    Args:
        candidate: Candidate workflow YAML.
        contract: Approved workflow YAML.
        label: Diagnostic prefix.

    Returns:
        A deterministic list containing at most one violation.
    """
    try:
        workflow = load_workflow(candidate)
        approved = load_workflow(contract)
    except yaml.YAMLError:
        return [f"{label}: source is not valid YAML"]
    if not isinstance(workflow, dict) or not all(
        isinstance(workflow.get(key), dict) for key in ("on", "permissions", "jobs")
    ):
        return [f"{label}: mapping contract is not approved"]
    for name, job in workflow["jobs"].items():
        if not isinstance(job, dict):
            return [f"{label}: {name} execution shape is not approved"]
        steps = job.get("steps")
        if steps is not None and (
            not isinstance(steps, list)
            or any(not isinstance(step, dict) for step in steps)
        ):
            return [f"{label}: {name} execution shape is not approved"]
    try:
        tags_match = _tagged_signature(candidate) == _tagged_signature(contract)
    except (TypeError, yaml.YAMLError):
        return [f"{label}: tagged contract is not approved"]
    semantics_match = _semantic(workflow) == _semantic(approved)
    if not tags_match or not semantics_match:
        return [f"{label}: semantic contract is not approved"]
    return []
