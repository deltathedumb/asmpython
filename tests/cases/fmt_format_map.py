# probes: str.format_map reads a mapping
# expect:
# ada
print("{name}".format_map({"name": "ada"}))
