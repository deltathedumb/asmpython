# tier: spec
# ref: library/sys.monitoring.html
# min-python: 3.12
# expect:
# True True
# True True
# conformance
# None
import sys

mon = sys.monitoring
print(hasattr(mon, "use_tool_id"), hasattr(mon, "register_callback"))
print(mon.events.CALL > 0, mon.events.NO_EVENTS == 0)
tool = mon.DEBUGGER_ID
mon.use_tool_id(tool, "conformance")
try:
    print(mon.get_tool(tool))
finally:
    mon.free_tool_id(tool)
print(mon.get_tool(tool))
