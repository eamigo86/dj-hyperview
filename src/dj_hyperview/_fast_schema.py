"""An XSD 1.1 registry class that answers repeated validation questions faster.

Validating one rendered document asks every element instance the same questions
again: which attributes are required, which carry a default, which type a behavior
selects, and whether an attribute value is allowed. The subclasses here answer them
once and keep the answer, without changing a single verdict.

They build on xmlschema internals (the builders and the component classes are not
documented API), so the importer uses them only with an xmlschema release they were
tested against, and treats any failure of this module as "use a plain XMLSchema11".
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from functools import cached_property
from typing import Any

from xmlschema import XMLSchema11
from xmlschema.validators.attributes import Xsd11Attribute, XsdAttributeGroup
from xmlschema.validators.builders import XsdBuilders
from xmlschema.validators.elements import Xsd11Element
from xmlschema.validators.simple_types import XsdSimpleType

# ponytail: a plain cap on the count and the length, with no eviction; an LRU if the
# memory held for the first values ever matters. The caps bound what a document can
# leave behind: MEMO_LIMIT values of at most MEMO_VALUE_LIMIT characters for each
# attribute declaration.
MEMO_LIMIT = 128
MEMO_VALUE_LIMIT = 64

_XS = "{http://www.w3.org/2001/XMLSchema}"
# The result depends on more than the value: an ID is recorded in the context to
# catch duplicates, an IDREF or an ENTITY refers to what the context holds, and a
# QName or a NOTATION resolves its prefix through the namespace context.
_CONTEXTUAL = frozenset(
    _XS + name
    for name in ("ID", "IDREF", "IDREFS", "ENTITY", "ENTITIES", "QName", "NOTATION")
)
# Checking one of these is as cheap as looking the value up.
_NO_GAIN = frozenset(_XS + name for name in ("string", "token", "normalizedString"))
# The only test the package generates for a registered behavior.
_ACTION_TEST = re.compile(r"@action = '([^'\"]*)'\Z")


def _derived_names(xsd_type: Any) -> set[str | None]:
    """Collect the names of a simple type and of every type it is built from.

    Args:
        xsd_type: Simple type of an attribute declaration.

    Returns:
        The names along its bases, list item types and union members.
    """
    names: set[str | None] = set()
    seen: set[int] = set()
    pending = [xsd_type]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        names.add(current.name)
        pending.append(getattr(current, "base_type", None))
        pending.append(getattr(current, "item_type", None))
        pending.extend(getattr(current, "member_types", None) or ())
    return names


class FastAttributeGroup(XsdAttributeGroup):
    """Attribute group that derives its required names and defaults once.

    The group is complete when the registry is built and is not changed after.
    """

    @cached_property
    def _required(self) -> tuple[str, ...]:
        return tuple(super().iter_required())

    @cached_property
    def _constraints(self) -> dict[bool, tuple[tuple[str, str], ...]]:
        base = super()
        return {use: tuple(base.iter_value_constraints(use)) for use in (True, False)}

    def iter_required(self) -> Iterator[str]:
        """Iterate the names of the required attributes.

        Returns:
            An iterator over the names computed on first use.
        """
        return iter(self._required)

    def iter_value_constraints(
        self, use_defaults: bool = True
    ) -> Iterator[tuple[str, str]]:
        """Iterate the attributes that have a fixed or default value.

        Args:
            use_defaults: Whether default values count next to fixed ones.

        Returns:
            An iterator over name and value pairs computed on first use.
        """
        return iter(self._constraints[bool(use_defaults)])


class FastAttribute(Xsd11Attribute):
    """Attribute declaration that does not decode a value it already accepted."""

    @cached_property
    def _memo(self) -> set[str] | None:
        """Hold the values accepted so far, or None when a value could differ.

        Returns:
            The set of accepted values, or None for a declaration whose verdict is
            not a function of the value alone.
        """
        xsd_type = self.type
        reusable = (
            self.fixed is None
            and self.default is None
            and isinstance(xsd_type, XsdSimpleType)
            and xsd_type.name not in _NO_GAIN
            and not _derived_names(xsd_type) & _CONTEXTUAL
        )
        # Assertion facets are taken to be functions of the value alone.
        return set() if reusable else None

    def raw_decode(self, obj: Any, validation: str, context: Any) -> Any:
        """Validate a value, skipping the work for one that was accepted before.

        Args:
            obj: Attribute value.
            validation: xmlschema validation mode.
            context: xmlschema validation context.

        Returns:
            The decoded value, or None for a remembered value, which only
            validation-only contexts ask for and ignore.
        """
        memo = self._memo
        if memo is None or not context.validation_only or context.patterns is not None:
            return super().raw_decode(obj, validation, context)
        if obj in memo:
            return None
        errors = len(context.errors)
        result = super().raw_decode(obj, validation, context)
        # Only an accepted value is kept, and a skipped validation accepts anything.
        # Long values are not kept, so what a document can leave behind is bounded.
        # Two threads may both pass the bound check, so it can be overshot by one
        # value per thread; a set never holds a wrong entry.
        if (
            validation != "skip"
            and len(context.errors) == errors
            and len(memo) < MEMO_LIMIT
            and isinstance(obj, str)
            and len(obj) <= MEMO_VALUE_LIMIT
        ):
            memo.add(obj)
        return result


class FastElement(Xsd11Element):
    """Element declaration that selects registered behavior types by dictionary."""

    @cached_property
    def _types_by_action(self) -> tuple[dict[str, Any], Any] | None:
        """Map each action to its type, or None when XPath has to decide.

        Returns:
            The types by action value and the type used for any other value, or
            None unless every test has the form the package generates.
        """
        types: dict[str, Any] = {}
        default = self.type
        for alternative in self.alternatives:
            if alternative.token is None:
                default = alternative.type
                break
            match = _ACTION_TEST.match(alternative.path)
            if match is None:
                return None
            types.setdefault(match[1], alternative.type)
        return (types, default) if types else None

    def get_alternative_type(
        self, elem: Any, inherited: dict[str, Any] | None = None
    ) -> Any:
        """Select the type of an element instance.

        Args:
            elem: Element instance, or the data of one that is being encoded.
            inherited: Inheritable attributes of the ancestors.

        Returns:
            The first alternative type whose test holds, as xmlschema selects it.
        """
        if self.alternatives and not inherited and hasattr(elem, "get"):
            selection = self._types_by_action
            if selection is not None:
                return selection[0].get(elem.get("action"), selection[1])
        return super().get_alternative_type(elem, inherited)


class FastXMLSchema11(XMLSchema11):
    """XSD 1.1 schema whose components are the fast ones."""

    builders = XsdBuilders(
        attribute_group_class=FastAttributeGroup,
        attribute_class=FastAttribute,
        element_class=FastElement,
    )
