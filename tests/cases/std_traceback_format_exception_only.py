# probes: traceback renders an exception line
# expect:
# ValueError: boom
import traceback

print(traceback.format_exception_only(ValueError, ValueError("boom"))[0], end="")
