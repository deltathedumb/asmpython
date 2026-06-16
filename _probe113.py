# probe113: complex string formatting edge cases
# repr vs str
class Foo:
    def __str__(self) -> str:
        return "Foo(str)"
    def __repr__(self) -> str:
        return "Foo(repr)"

f = Foo()
print(str(f))    # Foo(str)
print(repr(f))   # Foo(repr)

# f-string uses str() by default, !r uses repr
print(f"{f}")     # Foo(str)
print(f"{f!r}")   # Foo(repr)
print(f"{f!s}")   # Foo(str)

# Format with padding/alignment on non-str
n = 42
print(f"|{n:10}|")    # |        42|
print(f"|{n:<10}|")   # |42        |
print(f"|{n:^10}|")   # |    42    |
print(f"|{n:010}|")   # |0000000042|
print(f"|{n:+10}|")   # |       +42|

# String repeat in f-string
print(f"{'x' * 5}")    # xxxxx
print(f"{'-' * 10}")   # ----------
