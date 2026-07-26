# expect:
# [1, 2, 4, 3]
graph = {1: [2, 3], 2: [4], 3: [4], 4: []}
def dfs(node, visited):
    if node in visited:
        return
    visited.append(node)
    for n in graph[node]:
        dfs(n, visited)
v = []
dfs(1, v)
print(v)
# asmpython (beta/3.14.0) MISMATCH: prints '[]\n' (wrong).
