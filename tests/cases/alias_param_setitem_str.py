# probes: element assignment of a str through a parameter
# expect:
# ['replaced', 'second']
# replaced
def mutate(xs):
    xs[0] = "replaced"


a = ["first", "second"]
mutate(a)
print(a)
print(a[0])
