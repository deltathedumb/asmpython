# expect:
# 0
# 0
# 0
# 0

from typing import get_type_hints, get_args, get_origin, is_typeddict

print(get_type_hints(0))
print(len(get_args(0)))
print(get_origin(0))
print(is_typeddict(0))
