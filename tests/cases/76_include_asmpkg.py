# expect:
# 99
# 100

# Exercises include(): pull in answerpkg.asmpkg (a sibling of this file) and
# call its exported `pkg_answer` symbol. The package's NASM is concatenated
# into the program and its export resolves as a callable.

from asmpython.assembly import include

include("answerpkg")


def run():
    print(pkg_answer())
    print(pkg_answer() + 1)


run()
