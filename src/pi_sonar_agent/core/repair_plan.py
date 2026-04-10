"""Bridge module for legacy `core.repair_plan`."""

import sys as _sys
from importlib import import_module as _import_module

_sys.modules[__name__] = _import_module("core.repair_plan")
