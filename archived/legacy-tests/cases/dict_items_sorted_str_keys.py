# expect:
# a 1
# b 2
d = {"a": 1, "b": 2}
for k, v in sorted(d.items()):
    print(k, v)
# asmpython (beta/3.14.0) prints pointer addresses for k (e.g. "9540704 1"):
# string keys lose their str type when iterated via sorted(d.items()).
