# expect:
# 33
# 33
# [[1, 2], [1, 2]] 100
# 30 1 2

# A comprehension is its own scope, so its targets must not rebind a name in
# the ENCLOSING scope. Comprehension targets previously reused the enclosing
# slot, so a function whose locals were k=1, v=2 found them left as 10, 20
# afterwards and `s + k + v` returned 60 instead of 33.
#
# Four shapes, because they resolve names by different routes:
#   - enclosing locals in a def
#   - enclosing PARAMETERS in a def (same slots, different origin)
#   - nested comprehensions reusing one name, where the inner must not write
#     the outer's target either
#   - module scope, which the shadow fix in 306 already covered and which must
#     stay correct now that targets are renamed rather than shadowed
def with_locals():
    k = 1
    v = 2
    pairs = [(10, 20)]
    s = sum([k + v for k, v in pairs])
    return s + k + v


def with_params(k, v):
    pairs = [(10, 20)]
    s = sum([k + v for k, v in pairs])
    return s + k + v


print(with_locals())
print(with_params(1, 2))

x = 100
print([[x for x in [1, 2]] for x in [3, 4]], x)

pairs = [(10, 20)]
k = 1
v = 2
s = sum([k + v for k, v in pairs])
print(s, k, v)
