# expect:
# 2
class K:
    def __init__(self, v):
        self.v = v
    def __hash__(self):
        return self.v
    def __eq__(self, o):
        return self.v == o.v
s = {K(1), K(1), K(2)}
print(len(s))
# asmpython (beta/3.14.0) rejects at compile: [E055] set elements of type instance:K are not supported yet (sets are str/int-keyed in v1)
