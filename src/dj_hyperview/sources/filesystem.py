"""Consumer-owned filesystem template source."""

from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path

from dj_hyperview.exceptions import InvalidTemplateName

from .base import ResolvedTemplate, canonicalize_template_name


def _is_within_root(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


class FileSystemSource:
    """Resolve UTF-8 templates below configured directories."""

    def __init__(self, template_dirs: Iterable[str | Path] | None = None) -> None:
        if template_dirs is None:
            from dj_hyperview.conf import get_settings

            template_dirs = get_settings().template_dirs
        self.template_dirs = tuple(Path(path).resolve() for path in template_dirs)

    def resolve(self, name: str) -> ResolvedTemplate | None:
        canonical = canonicalize_template_name(name)
        for root in self.template_dirs:
            candidate = (root / canonical).resolve()
            if not _is_within_root(candidate, root):
                raise InvalidTemplateName(name)
            if not candidate.is_file():
                continue
            data = candidate.read_bytes()
            return ResolvedTemplate(
                name=canonical,
                content=data.decode("utf-8"),
                origin=candidate.as_uri(),
                source="filesystem",
                revision=sha256(data).hexdigest(),
            )
        return None
