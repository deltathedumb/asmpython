# expect:
# start
# Warning: test warning
# after warn
# done

import warnings

print("start")
warnings.warn("test warning", 0)
print("after warn")
warnings.filterwarnings("ignore")
print("done")
