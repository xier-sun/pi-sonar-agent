"""Compatibility package for `pi_sonar_agent` imports.

The project currently stores modules directly under `src/` (e.g. `src/main.py`,
`src/integrations/...`). This package extends its import search path so existing
`pi_sonar_agent.*` imports resolve without moving every module.
"""

from pathlib import Path

# Allow importing submodules from the legacy flat `src/` layout.
_legacy_src_root = Path(__file__).resolve().parent.parent
if str(_legacy_src_root) not in __path__:  # type: ignore[name-defined]
    __path__.append(str(_legacy_src_root))  # type: ignore[name-defined]

__version__ = "1.0.0"

