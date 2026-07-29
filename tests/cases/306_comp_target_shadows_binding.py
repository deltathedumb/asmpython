# expect:
# 10 20
# [10]
# [20]
# [30]
# [0, 1]
# 60
# {'1': 1, '2': 2}
# {'0': 'p', '1': 'q'}

# A comprehension's loop variables are its OWN, even when a module global of
# the same name exists. Four separate lowering paths in ir_lower.py bind
# comprehension targets, and each got this wrong in its own way; all four are
# covered below so a fix to one cannot silently regress another.
#
# The mechanism: _store_loop_target resolves a bare name through _name_ptr ->
# _is_global_name, which answers "local" only while that name sits in an active
# ctx.comprehension_shadows set. Three paths pushed the shadow set AFTER
# emitting the target stores, so a target sharing a name with a module global
# was STORED into .bss while the body -- shadowed by then -- read a
# never-written local slot and saw 0. The dict-comprehension path pushed no
# shadow set at all, so it both stored to and read from the global.
#
# Observed before the fix:
#   [k for k, v in pairs]                    -> [0]        want [10]
#   [i for i, x in enumerate(xs)]            -> [0, 0]     want [0, 1]
#   [k + v for k, v in pairs for w in [1,2]] -> 0          want 60
#   {str(n): n for n in [1, 2]}              -> {'7': 7}   want two entries
#
# Wrong for exactly the names that were already bound and correct for fresh
# ones, which is why it survived: every one of these compiles clean.
pairs = [(10, 20)]
k = 1
v = 2

for k, v in pairs:
    print(k, v)

# generic list comprehension, tuple targets
print([k for k, v in pairs])
print([v for k, v in pairs])
print([k + v for k, v in pairs])

# enumerate path
xs = ["p", "q"]
i = 99
x = "zz"
print([i for i, x in enumerate(xs)])

# multi-clause path (two `for`s)
w = 77
print(sum([k + v for k, v in pairs for w in [1, 2]]))

# dict comprehension, single target
n = 7
print({str(n): n for n in [1, 2]})

# dict comprehension, enumerate
print({str(i): x for i, x in enumerate(xs)})
