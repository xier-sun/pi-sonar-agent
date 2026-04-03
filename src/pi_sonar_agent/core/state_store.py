"""Bridge module for legacy `core.state_store`."""

import sys as _sys
from importlib import import_module as _import_module

_sys.modules[__name__] = _import_module('core.state_store')
