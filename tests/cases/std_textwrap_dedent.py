# probes: textwrap.dedent strips the common prefix
# expect:
# a
# b
import textwrap

print(textwrap.dedent("    a\n    b\n"), end="")
