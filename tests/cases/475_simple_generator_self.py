# expect:
# 1


class Node:
    def walk(self):
        yield self


print(len(Node().walk()))
