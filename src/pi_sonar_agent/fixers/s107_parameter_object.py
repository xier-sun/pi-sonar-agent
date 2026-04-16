"""Bridge module for legacy `fixers.s107_parameter_object`."""

import sys as _sys
from importlib import import_module as _import_module

_sys.modules[__name__] = _import_module('fixers.s107_parameter_object')
