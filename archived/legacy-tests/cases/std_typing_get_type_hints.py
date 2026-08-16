# probes: get_type_hints resolves annotations
# expect:
# int
# str
# bool
import typing


def annotated(a: int, b: str) -> bool:
    return True


hints = typing.get_type_hints(annotated)
print(hints["a"].__name__)
print(hints["b"].__name__)
print(hints["return"].__name__)
