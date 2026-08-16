# expect:
# True
# False
# True
# False
# True

# Set literals are dicts keyed by their members; `in` / `not in` are str-key
# membership tests. (v1 sets model membership only.)

keywords = {"def", "return", "if", "while"}
print("def" in keywords)        # True
print("class" in keywords)      # False
print("while" in keywords)      # True
print("def" not in keywords)    # False
print("xyz" not in keywords)    # True
