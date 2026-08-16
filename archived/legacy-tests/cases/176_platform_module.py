# expect:
# x86_64
# asmpython

from platform import machine, python_implementation

print(machine())
print(python_implementation())
