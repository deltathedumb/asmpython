"""IANA time zones.

THERE IS NO TIME ZONE DATABASE IN A COMPILED IMAGE, and this module says so
rather than pretending. `ZoneInfo("UTC")` raises `ZoneInfoNotFoundError`,
which is exactly what CPython does on a system with no tzdata and no
`tzdata` package -- so a program that guards its zone lookup behaves the same
here, and one that does not fails in the same place with the same error.

Shipping the database would mean embedding several hundred kilobytes of
compiled rules in every binary, and inventing offsets would be a wrong answer.
"""


class ZoneInfoNotFoundError(KeyError):
    """No rules for this zone could be found."""


class ZoneInfo:
    def __init__(self, key):
        raise ZoneInfoNotFoundError("No time zone found with key " + str(key))

    @staticmethod
    def no_cache(key):
        raise ZoneInfoNotFoundError("No time zone found with key " + str(key))

    @staticmethod
    def from_file(fobj, key=None):
        raise ZoneInfoNotFoundError("no tzdata")

    @staticmethod
    def clear_cache(only_keys=None):
        return None


def available_timezones():
    """THE EMPTY SET, which is the honest answer for an image with no rules."""
    return set()


def reset_tzpath(to=None):
    return None


TZPATH = ()
