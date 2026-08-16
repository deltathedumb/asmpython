# guards: container_field_compat_fixes
# expect:
# 3
# 4
# 6
class Holder:
    def __init__(self):
        self.items = [1, 2, 3]

    def copy_items(self):
        return list(self.items)


h = Holder()
copied = h.copy_items()
copied.append(4)
print(len(h.items))
print(len(copied))
total = 0
for v in h.items:
    total = total + v
print(total)
