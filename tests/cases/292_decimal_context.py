# expect:
# 28
# ROUND_HALF_EVEN
# 9

from decimal import Context, getcontext, DefaultContext, ExtendedContext, BasicContext

ctx = getcontext()
print(ctx.prec)
print(ctx.rounding)
print(ExtendedContext.prec)
