# probes: a logger drops records below its level
# expect:
# kept
import io
import logging

buf = io.StringIO()
handler = logging.StreamHandler(buf)
handler.setFormatter(logging.Formatter("%(message)s"))
logger = logging.getLogger("levels")
logger.setLevel(logging.WARNING)
logger.addHandler(handler)
logger.warning("kept")
logger.info("dropped")
print(buf.getvalue(), end="")
