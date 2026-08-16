# expect:
# 10
# 20
# 99
# b
# 7
# 2

# Dict values may themselves be dicts or lists (stored as heap pointers in the
# uniform slot). A chained read recovers the leaf type one nesting level deep:
# dict-of-dict-of-int, dict-of-dict-of-str, and dict-of-list-of-int.

grid = {"a": {"x": 10, "y": 20}, "b": {"x": 99, "y": 0}}
print(grid["a"]["x"])      # 10
print(grid["a"]["y"])      # 20
print(grid["b"]["x"])      # 99

words = {"k": {"inner": "b"}}
print(words["k"]["inner"]) # b  (str leaf recovered through two dict levels)

lists = {"nums": [7, 8, 9], "more": [1, 2]}
print(lists["nums"][0])    # 7
print(len(lists["more"]))  # 2
