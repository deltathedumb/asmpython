# probes: __index__ lets an object index a sequence
# expect:
# 30
class Two:
    def __index__(self):
        return 2


print([10, 20, 30][Two()])
