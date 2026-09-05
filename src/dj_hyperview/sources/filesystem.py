"""Consumer-owned filesystem template source."""

from collections.abc import Iterable
from errno import ELOOP, ENAMETOOLONG, ENOENT, ENOTDIR
from hashlib import sha256
from os import PathLike
from pathlib import Path

from dj_hyperview.exceptions import (
    HyperviewConfigurationError,
    InvalidTemplateName,
    SourceUnavailable,
    TemplateValidationError,
)

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
        """Initialize a filesystem source.

        Args:
            template_dirs: Ordered template roots, or None to load settings.

        Raises:
            HyperviewConfigurationError: If template_dirs is a scalar path value.
        """
        if template_dirs is None:
            from dj_hyperview.conf import get_settings

            template_dirs = get_settings().template_dirs
        if isinstance(template_dirs, (str, bytes, PathLike)):
            raise HyperviewConfigurationError(
                ["FileSystemSource template_dirs must be an iterable of paths"]
            )
        self.template_dirs = tuple(Path(path).absolute() for path in template_dirs)

    def resolve(self, name: str) -> ResolvedTemplate | None:
        """Resolve one canonical name from the first matching root.

        Args:
            name: Canonical template name.

        Returns:
            The resolved template from the first matching root, otherwise a miss.

        Raises:
            InvalidTemplateName: If path resolution escapes a configured root.
            SourceUnavailable: If filesystem access fails unexpectedly.
            TemplateValidationError: If template content is not valid UTF-8.
        """
        canonical = canonicalize_template_name(name)
        for configured_root in self.template_dirs:
            try:
                root = configured_root.resolve()
                candidate = (root / canonical).resolve()
                if not _is_within_root(candidate, root):
                    raise InvalidTemplateName(name)
                if not candidate.is_file():
                    continue
                data = candidate.read_bytes()
            except RuntimeError:
                continue
            except OSError as error:
                if error.errno in {ELOOP, ENAMETOOLONG, ENOENT, ENOTDIR}:
                    continue
                raise SourceUnavailable("filesystem", "read failed") from None
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError:
                raise TemplateValidationError(
                    "invalid_encoding", "template source must be UTF-8"
                ) from None
            return ResolvedTemplate(
                name=canonical,
                content=content,
                origin=candidate.as_uri(),
                source="filesystem",
                revision=sha256(data).hexdigest(),
            )
        return None
