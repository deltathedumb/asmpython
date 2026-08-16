# guards: class_value_compat_fixes
# expect:
# A
# A
# B
class A:
    def tag(self):
        return "A"


class B:
    def tag(self):
        return "B"


KINDS = (A, B)

first = KINDS[0]
print(first().tag())

for kind in KINDS:
    print(kind().tag())
