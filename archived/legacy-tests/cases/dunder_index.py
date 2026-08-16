# expect:
# 30
class Idx:
    def __index__(self):
        return 2
arr = [10, 20, 30, 40]
print(arr[Idx()])
# asmpython (beta/3.14.0) runtime failure: exit 0x1
