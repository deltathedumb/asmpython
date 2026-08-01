# tier: spec
# ref: library/contextvars.html
# expect:
# default
# set
# default
# default
import contextvars

var = contextvars.ContextVar("var", default="default")
print(var.get())
token = var.set("set")
print(var.get())
var.reset(token)
print(var.get())

ctx = contextvars.copy_context()
print(ctx.run(var.get))
