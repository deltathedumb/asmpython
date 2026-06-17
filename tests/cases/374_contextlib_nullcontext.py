# expect:
# 42
# done

from contextlib import nullcontext

with nullcontext(42) as val:
    print(val)
print("done")
