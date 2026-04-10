"""Bridge module for legacy `core.tool_surface`."""

import sys as _sys
from importlib import import_module as _import_module

_sys.modules[__name__] = _import_module("core.tool_surface")
