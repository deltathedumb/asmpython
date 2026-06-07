# expect:
# 1
# 2
# 3
# 4

# Relative import — accepted as a no-op for self-host.
from .errors import SomeError, AnotherError
from ..util import helper
from . import x, y as z

# Absolute import of a CPython module that isn't in mamba's stdlib registry —
# also accepted as a no-op so source can be type-checked.
import os
import os.path as p
import sys
from os import getcwd

print(1)
print(2)
print(3)
print(4)
