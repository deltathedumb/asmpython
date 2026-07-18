# ext: assign_decorators
# expect-error: only supports a plain function name, not a dotted attribute

import math

@math.floor
x = 5
