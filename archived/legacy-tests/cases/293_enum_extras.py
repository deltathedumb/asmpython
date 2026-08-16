# expect:
# unique
# StrEnum

from enum import unique, StrEnum, EnumMeta

@unique
class Color:
    RED: int = 1
    GREEN: int = 2

print("unique")
print("StrEnum")
