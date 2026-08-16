# guards: empty_collection_compat_fixes
# expect:
# 0
# 0
class Emitter:
    def __init__(self):
        self.listeners = []

    def fire(self):
        count = 0
        for listener in self.listeners:
            count = count + 1
        return count


e = Emitter()
print(e.fire())
print(len(e.listeners))
