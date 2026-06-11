from asmpython._compiler.sema import STDLIB_BINDINGS

print(len(STDLIB_BINDINGS))
m = STDLIB_BINDINGS["math"]
print(len(m))
