"""Bridge module for legacy `core.permission_manager`."""

import sys as _sys
from importlib import import_module as _import_module

_sys.modules[__name__] = _import_module("core.permission_manager")
