# expect:
# root
#   a
#   b
#     c
tree = {'root': {'a': {}, 'b': {'c': {}}}}
def show(node, depth=0):
    for key in sorted(node):
        print('  ' * depth + key)
        show(node[key], depth + 1)
show(tree)
# asmpython (beta/3.14.0) rejects at compile: [E012] unsupported operand type for +: str + int
