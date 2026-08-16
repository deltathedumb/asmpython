# expect:
# WARNING:root:alert
# ERROR:root:fail
# CRITICAL:root:crash
# DEBUG:root:debug msg
# INFO:root:info msg

import logging

logging.warning("alert")
logging.error("fail")
logging.critical("crash")
logging.basicConfig(level=logging.DEBUG)
logging.debug("debug msg")
logging.info("info msg")
