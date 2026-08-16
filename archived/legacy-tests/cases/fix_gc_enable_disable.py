# probes: gc reports and toggles its enabled state
# expect:
# False
# True
import gc

was_enabled = gc.isenabled()
gc.disable()
print(gc.isenabled())
gc.enable()
print(gc.isenabled())
if not was_enabled:
    gc.disable()
