import sys
import select
import tty
import termios
import time
import threading

arrow_up = "\x1b[A"
arrow_UP = "\x1b[2A"
arrow_down = "\x1b[B"
arrow_right = "\x1b[C"
arrow_left = "\x1b[D"
shift_arrow_up = "\x1b[1;2A"
shift_arrow_down = "\x1b[1;2B"
shift_arrow_right = "\x1b[1;2C"
shift_arrow_left = "\x1b[1;2D"
ctrl_arrow_up = "\x1b[1;5A"
ctrl_arrow_down = "\x1b[1;5B"
ctrl_arrow_right = "\x1b[1;5C"
ctrl_arrow_left = "\x1b[1;5D"


def isData():
    return select.select([sys.stdin], [], [], 1) == ([sys.stdin], [], [])


def getc():
    """Non blocking get character"""
    old_settings = termios.tcgetattr(sys.stdin)
    c = None
    try:
        tty.setcbreak(sys.stdin.fileno())
        if isData():
            c = sys.stdin.read(1)
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    return c


class _InputHookContext:
    """Minimal stand-in for prompt_toolkit's InputHookContext, which is all
    IPython's GUI input hooks (qt, tk, gtk, ...) look at."""

    def __init__(self, fd):
        self._fd = fd

    def fileno(self):
        return self._fd

    def input_is_ready(self):
        return select.select([self._fd], [], [], 0)[0] != []


def _gui_input_hook():
    """The input hook IPython runs while it waits at the prompt (set by
    ``%gui qt`` / ``%matplotlib qt`` / eco's startup), or None.

    Only usable from the main thread of a terminal IPython session."""
    if threading.current_thread() is not threading.main_thread():
        return None
    try:
        from IPython import get_ipython

        shell = get_ipython()
    except Exception:
        return None
    if shell is None or getattr(shell, "_inputhook", None) is None:
        return None
    hook = getattr(shell, "inputhook", None)
    return hook if callable(hook) else None


def _qt_app():
    if threading.current_thread() is not threading.main_thread():
        return None
    qtw = sys.modules.get("qtpy.QtWidgets") or sys.modules.get("PyQt5.QtWidgets")
    if qtw is None:
        return None
    try:
        return qtw.QApplication.instance()
    except Exception:
        return None


def _wait_for_stdin():
    """Block until stdin is readable, but keep GUI event loops running.

    A plain select/sleep loop here starves the Qt event loop for as long as a
    tweak runs, freezing every live plot / camera viewer. Instead we do what
    IPython itself does at the prompt: run the active GUI input hook, which
    spins the GUI event loop until a key arrives (zero CPU for qt). Without a
    hook but with a QApplication around, pump it every 20 ms."""
    fd = sys.stdin.fileno()
    hook = _gui_input_hook()
    if hook is not None:
        ctx = _InputHookContext(fd)
        try:
            while not ctx.input_is_ready():
                hook(ctx)
            return
        except Exception:
            pass  # fall through to the plain wait below
    app = _qt_app()
    while not select.select([fd], [], [], 0.02 if app is not None else 1)[0]:
        if app is not None:
            app.processEvents()


def wait_input():
    """wait for a character and returns it"""
    old_settings = termios.tcgetattr(sys.stdin)
    c = None
    try:
        tty.setcbreak(sys.stdin.fileno())
        _wait_for_stdin()
        c = sys.stdin.read(1)
        if c == "\x1b":
            cc = sys.stdin.read(2)
            if cc == "[1":
                c = c + cc + sys.stdin.read(3)
            else:
                c = c + cc
        return c
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    return c


def wait_char(char="q"):
    """wait for a specific character, default is q"""
    c = None
    while c != char:
        c = wait_input()


#    print "got |%s| but waiting for |%s|" % (c,char)


class KeyPress:
    def __init__(self, esc_key="q"):
        self.esc_key = esc_key
        self.last_key = None

    def waitkey(self):
        a = wait_input()
        self.last_key = a
        return a

    def isu(self):
        return self.last_key == arrow_up

    def isd(self):
        return self.last_key == arrow_down

    def isl(self):
        return self.last_key == arrow_left

    def isr(self):
        return self.last_key == arrow_right

    def issu(self):
        return self.last_key == shift_arrow_up

    def issd(self):
        return self.last_key == shift_arrow_down

    def issl(self):
        return self.last_key == shift_arrow_left

    def issr(self):
        return self.last_key == shift_arrow_right

    def iscu(self):
        return self.last_key == ctrl_arrow_up

    def iscd(self):
        return self.last_key == ctrl_arrow_down

    def iscl(self):
        return self.last_key == ctrl_arrow_left

    def iscr(self):
        return self.last_key == ctrl_arrow_right

    def isq(self):
        return self.last_key == self.esc_key

    def iskey(self, what):
        return self.last_key == what


# wait_char('\x37')
# s=wait_input()
# print (s==arrow_up)

if __name__ == "__main__":
    while 1:
        print("press a key (q to exit)")
        a = wait_input()
        print("|%s|" % a)
        if a == "q":
            break
