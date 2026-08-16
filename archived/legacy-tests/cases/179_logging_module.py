# expect:
# WARNING:root:hello
# ERROR:myapp:something failed

import logging

logging.warning("hello")

log = logging.getLogger("myapp")
log.error("something failed")
