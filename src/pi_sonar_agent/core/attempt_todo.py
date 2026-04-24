"""Bridge module for legacy `core.attempt_todo`."""

import sys as _sys
from importlib import import_module as _import_module

_sys.modules[__name__] = _import_module("core.attempt_todo")
