# probes: the logging module emits through a handler
# expect:
# INFO:hello
import io
import logging

buf = io.StringIO()
handler = logging.StreamHandler(buf)
handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
logger = logging.getLogger("probe")
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.info("hello")
logger.debug("hidden")
print(buf.getvalue(), end="")
