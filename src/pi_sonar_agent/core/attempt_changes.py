"""Bridge module for legacy `core.attempt_changes`."""

from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("core.attempt_changes")
