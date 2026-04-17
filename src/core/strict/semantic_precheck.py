"""Strict-mode compatibility wrapper for `core.semantic_precheck`."""

import sys as _sys
from importlib import import_module as _import_module

_sys.modules[__name__] = _import_module("core.semantic_precheck")
