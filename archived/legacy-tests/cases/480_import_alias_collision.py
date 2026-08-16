# expect:
# 9
from import_alias_collision_helper import transform as original_transform


def transform(value: int) -> int:
    return original_transform(value) + 1


print(transform(4))
