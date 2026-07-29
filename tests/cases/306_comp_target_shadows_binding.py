# expect:
# 10 20
# [10]
# [20]
# [30]

# KNOWN FAILING at fa7087f7 -- tracked, not introduced here.
#
# asmpython (beta/3.14.0) MISMATCH: prints '10 20', '[0]', '[0]', '[0]'.
#
# A comprehension whose TUPLE-UNPACKING target reuses a name already bound in
# the enclosing scope reads 0 for exactly the names that were already bound:
#
#   k = 1; v = 2
#   [k     for k, v in pairs]  -> [0]   (want [10])
#   [v     for k, v in pairs]  -> [0]   (want [20])
#   [k + v for k, v in pairs]  -> [0]   (want [30])
#
# Bind only `k` beforehand and it prints [20] (v correct, k reads 0); bind only
# `v` and it prints [10]. Fresh names are unaffected, and the single-target form
# `[n for n in xs]` with `n` pre-bound is correct -- so this is specific to the
# tuple-unpacking path.
#
# Cause (codegen.py, the comprehension slot pre-pass): _cl_define SKIPS
# allocation for a name that is a module global, because globals live in .bss.
# The single-variable branch works around that by force-allocating a mangled
# `__compvar_<id>_<name>` slot and temporarily exposing it under the real name
# while the body is generated. The `if expr.targets:` branch just calls
# _cl_define per name and never does either, so the unpack store and the body's
# read disagree about where the value lives.
#
# Fixing it means extending that mangling to N targets plus the matching
# save/restore in _gen_comprehension, _gen_comprehension_enumerate and the
# dict-comprehension path. Deliberately left out of the P026 change set so the
# two remain separately measurable.
pairs = [(10, 20)]
k = 1
v = 2

for k, v in pairs:
    print(k, v)

print([k for k, v in pairs])
print([v for k, v in pairs])
print([k + v for k, v in pairs])
