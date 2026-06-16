# probe103: complex recursive patterns, tree-like structures
class TreeNode:
    def __init__(self, val: int) -> None:
        self.val = val
        self.left = None
        self.right = None

def insert(root: "TreeNode", val: int) -> "TreeNode":
    if root is None:
        return TreeNode(val)
    if val < root.val:
        root.left = insert(root.left, val)
    else:
        root.right = insert(root.right, val)
    return root

def inorder(root: "TreeNode") -> list:
    if root is None:
        return []
    result = inorder(root.left)
    result.append(root.val)
    result.extend(inorder(root.right))
    return result

def height(root: "TreeNode") -> int:
    if root is None:
        return 0
    lh = height(root.left)
    rh = height(root.right)
    return 1 + (lh if lh > rh else rh)

root = None
for v in [5, 3, 7, 1, 4, 6, 8]:
    root = insert(root, v)

result = inorder(root)
print(result)       # [1, 3, 4, 5, 6, 7, 8]
print(result[0])    # 1
print(result[6])    # 8
print(height(root)) # 3
