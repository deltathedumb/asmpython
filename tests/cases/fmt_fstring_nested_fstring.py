# probes: an f-string may contain another f-string
# expect:
# hello world
inner = "world"
print(f"{f'hello {inner}'}")
