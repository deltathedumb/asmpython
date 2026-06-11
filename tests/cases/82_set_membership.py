# expect:
# 1
# 0
# 1
# 0
# 1

# Set literals are dicts keyed by their members; `in` / `not in` are str-key
# membership tests. (v1 sets model membership only.)

keywords = {"def", "return", "if", "while"}
print("def" in keywords)        # 1
print("class" in keywords)      # 0
print("while" in keywords)      # 1
print("def" not in keywords)    # 0
print("xyz" not in keywords)    # 1
