# probes: iterating .items() unpacks two names
# expect:
# ['a1', 'b2']
source = {"a": 1, "b": 2}
print([k + str(v) for k, v in source.items()])
