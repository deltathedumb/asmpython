from asmpython._compiler.sema import STDLIB_BINDINGS
from asmpython.stdlib.math import BINDINGS

print(len(BINDINGS))
print(len(STDLIB_BINDINGS))
m = STDLIB_BINDINGS["math"]
print(len(m))
