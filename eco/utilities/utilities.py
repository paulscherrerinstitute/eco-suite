from time import sleep
import sys, select
from threading import Thread

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Any


class TimeoutPath:
    executor = ThreadPoolExecutor(max_workers=1)

    def __init__(self, *args, timeout: float = 1, **kwargs):
        self._path = Path(*args, **kwargs)
        self.timeout = timeout

    def exists(self) -> bool:
        future = TimeoutPath.executor.submit(self._path.exists)
        try:
            return future.result(self.timeout)
        except TimeoutError:
            return False

    def get_path(self) -> Path:
        return self._path

    def __getattr__(self, name: str) -> Any:
        return getattr(self._path, name)

    def __str__(self) -> str:
        return str(self._path)


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
