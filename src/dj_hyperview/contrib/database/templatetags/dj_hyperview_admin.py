"""Cross-version Django admin template filters."""

from collections.abc import Iterable, Iterator
from types import GeneratorType

from django import template
from django.utils.html import conditional_escape
from django.utils.safestring import SafeString, mark_safe
from django.utils.translation import ngettext

register = template.Library()


def _walk_items(
    item_list: Iterable[object],
) -> Iterator[tuple[object, Iterable[object] | None]]:
    iterator = iter(item_list)
    try:
        item = next(iterator)
        while True:
            try:
                next_item = next(iterator)
            except StopIteration:
                yield item, None
                break
            if isinstance(next_item, (list, tuple, GeneratorType)):
                yield item, next_item
                item = next(iterator)
                continue
            yield item, None
            item = next_item
    except StopIteration:
        return


@register.filter(is_safe=True, needs_autoescape=True)
def truncated_unordered_list(
    value: Iterable[object],
    max_items: int | str | None,
    autoescape: bool = True,
) -> SafeString:
    """Render a safely escaped nested list with an optional item limit.

    Args:
        value: Self-nested sequence used by Django's deletion collector.
        max_items: Maximum objects to render, or None for no limit.
        autoescape: Whether item labels require conditional escaping.

    Returns:
        Safe HTML list items without the outer unordered-list element.

    Raises:
        ValueError: If max_items cannot be interpreted as an integer.
    """
    unlimited = max_items is None
    limit = 0 if unlimited else int(max_items)
    if not unlimited and limit <= 0:
        return mark_safe("")

    escape = conditional_escape if autoescape else str
    item_count = 0

    def format_items(items: Iterable[object], tabs: int = 1) -> str:
        nonlocal item_count
        indent = "\t" * tabs
        output: list[str] = []
        for item, children in _walk_items(items):
            item_count += 1
            child_markup = ""
            if children:
                child_items = format_items(children, tabs + 1)
                child_markup = f"\n{indent}<ul>\n{child_items}\n{indent}</ul>\n{indent}"
            if unlimited or item_count <= limit:
                output.append(f"{indent}<li>{escape(item)}{child_markup}</li>")
        return "\n".join(output)

    rendered = format_items(value)
    if not unlimited and item_count > limit:
        remaining = item_count - limit
        message = ngettext(
            "…and %(count)d more object.",
            "…and %(count)d more objects.",
            remaining,
        ) % {"count": remaining}
        rendered = f"{rendered}\n\t<li>{message}</li>"
    return mark_safe(rendered)
