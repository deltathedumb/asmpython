# probes: an f-string may subscript a dict
# expect:
# ada-2
row = {"name": "ada", "n": 2}
print(f"{row['name']}-{row['n']}")
