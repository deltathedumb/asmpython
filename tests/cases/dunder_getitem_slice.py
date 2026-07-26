# expect:
# [2, 3]
class Seq:
    def __init__(self, data):
        self.data = data
    def __getitem__(self, key):
        return self.data[key]
s = Seq([1, 2, 3, 4, 5])
print(s[1:3])
# asmpython (beta/3.14.0) rejects at compile: [E017] slicing not supported on instance:Seq
