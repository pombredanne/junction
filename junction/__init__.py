from __future__ import absolute_import

from .node import Node


VERSION = (0, 1, 0, "")
__version__ = ".".join(filter(None, map(str, VERSION)))

Node.VERSION = VERSION
