# probes: a joined thread has finished its work
# expect:
# ['done']
# False
import threading

result = []


def work():
    result.append("done")


t = threading.Thread(target=work)
t.start()
t.join()
print(result)
print(t.is_alive())
