"""Bridge module for legacy `core.simple_post_check`."""

import sys as _sys
from importlib import import_module as _import_module

_sys.modules[__name__] = _import_module("core.simple_post_check")
