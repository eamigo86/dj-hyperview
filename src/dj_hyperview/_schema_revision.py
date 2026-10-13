"""The one place that names the active corrected schema revision.

A leaf module with no package imports, so the schema loaders can all share it
without an import cycle. Moving to a new upstream version or revision means
editing the two values below and shipping the matching directory.
"""

from pathlib import Path

SCHEMAS_ROOT = Path(__file__).with_name("schemas")
SCHEMA_VERSION = "0.111.0"
SCHEMA_REVISION = "r3"
# Holds the corrected entry point, its catalog and its manifest. The unchanged
# upstream files it extends live one level up, in the version directory.
ACTIVE_SCHEMA_ROOT = SCHEMAS_ROOT / SCHEMA_VERSION / SCHEMA_REVISION
SCHEMA_IDENTITY = f"hyperview-{SCHEMA_VERSION}-corrected-{SCHEMA_REVISION}"
