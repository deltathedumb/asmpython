# guards: issubclass_compat_fixes
# expect:
# True
# True
# False
# False
# True
# False
class Root:
    pass


class Mid(Root):
    pass


class Leaf(Mid):
    pass


class Other:
    pass


print(issubclass(Leaf, Root))
print(issubclass(Mid, Root))
print(issubclass(Root, Leaf))
print(issubclass(Other, Root))
print(issubclass(Leaf, (Other, Root)))
print(issubclass(Other, (Root, Mid)))
