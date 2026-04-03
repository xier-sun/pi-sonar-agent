"""Bridge module for legacy `reporting.html_report`."""

import sys as _sys
from importlib import import_module as _import_module

_sys.modules[__name__] = _import_module('reporting.html_report')
