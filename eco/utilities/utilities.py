from time import sleep
import sys, select
from threading import Thread


class PropagatingThread(Thread):
    def run(self):
        self.exc = None
        try:
            if hasattr(self, "_Thread__target"):
                # Thread uses name mangling prior to Python 3.
                self.ret = self._Thread__target(
                    *self._Thread__args, **self._Thread__kwargs
                )
            else:
                self.ret = self._target(*self._args, **self._kwargs)
        except BaseException as e:
            self.exc = e

    def join(self):
        super(PropagatingThread, self).join()
        if self.exc:
            raise self.exc
        return self.ret


# TODO

# _wait_strs = '\|/-\|/-'

# class WaitInput:
#     def __init__(self,text,wait_time=5,update_interval=1):
#         self.text = text
#         self.wait_time=wait_time


#     def start(self):
#         resttime = self.wait_time
#         while resttime>0:
#             print(f"You have {resttime} seconds to answer!")
#             i, o, e = select.select( [sys.stdin], [], [], 2 )

#         if (i):
#             print("You said", sys.stdin.readline().strip())
#         else:
#             print("You said nothing!")
