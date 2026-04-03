"""Bridge module for legacy `fixers.roslyn`."""

import sys as _sys
from importlib import import_module as _import_module

_sys.modules[__name__] = _import_module('fixers.roslyn')
