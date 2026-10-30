"""Null-entry strategy package (T2-18).

The leading underscore keeps it out of the strategy registry: it is a test
instrument, not a tradable strategy.
"""

from .strategy import NullEntryStrategy

__all__ = ["NullEntryStrategy"]
