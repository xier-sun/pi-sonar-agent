"""Bridge module for legacy `core.memory.evidence_state` imports."""

import sys as _sys
from importlib import import_module as _import_module

_sys.modules[__name__] = _import_module("core.memory.evidence_state")
