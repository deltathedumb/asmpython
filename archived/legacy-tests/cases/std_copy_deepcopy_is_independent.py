# probes: deepcopy does not alias nested state
# expect:
# [1, 2]
# [1, 2, 3]
import copy

original = {"xs": [1, 2]}
clone = copy.deepcopy(original)
clone["xs"].append(3)
print(original["xs"])
print(clone["xs"])
