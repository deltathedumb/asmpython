# expect:
# 3
class Custom:
    def __index__(self):
        return 3
print(list(range(10))[Custom()])
# asmpython (beta/3.14.0) runtime failure: exit 0x1
