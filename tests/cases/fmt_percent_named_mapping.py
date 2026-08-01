# probes: %(name)s reads from a mapping
# expect:
# hi ada
print("%(greet)s %(name)s" % {"greet": "hi", "name": "ada"})
