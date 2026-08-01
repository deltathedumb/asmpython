# guards: global_return_flow_compat_fixes
# expect:
# item-1
# item-42
class Catalog:
    def __init__(self):
        self.prefix = "item-"

    def label(self, n):
        return self.prefix + str(n)


CATALOG = Catalog()


def make_label(n):
    return CATALOG.label(n)


print(make_label(1))
print(make_label(42))
