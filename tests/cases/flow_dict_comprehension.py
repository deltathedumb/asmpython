# probes: a dict comprehension builds keys and values
# expect:
# {'a': 1, 'bb': 2}
print({k: len(k) for k in ["a", "bb"]})
