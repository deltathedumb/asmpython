# expect:
# somnia.Root


class Node:
    @property
    def type_name(self):
        return "somnia.Root"


print(Node().type_name)
