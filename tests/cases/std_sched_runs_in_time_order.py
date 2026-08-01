# probes: sched runs due events in timestamp order
# expect:
# first
# second
import sched

scheduler = sched.scheduler(lambda: 100.0, lambda _: None)
scheduler.enterabs(2.0, 1, print, ("second",))
scheduler.enterabs(1.0, 1, print, ("first",))
scheduler.run()
