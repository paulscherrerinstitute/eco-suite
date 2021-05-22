import elog as _elog_ha
from getpass import getuser as _getuser
from getpass import getpass as _getpass
import os, datetime, subprocess


def getDefaultElogInstance(url, **kwargs):
    from pathlib import Path

    home = str(Path.home())
    if not ("user" in kwargs.keys()):
        kwargs.update(dict(user=_getuser()))

    if not ("password" in kwargs.keys()):
        try:
            with open(os.path.join(home, ".elog_psi"), "r") as f:
                _pw = f.read().strip()
        except:
            print("Enter elog password for user: %s" % kwargs["user"])
            _pw = _getpass()
        kwargs.update(dict(password=_pw))

    return _elog_ha.open(url, **kwargs), kwargs["user"]


class Elog:
    def __init__(self, url, screenshot_directory="", **kwargs):
        self._log, self.user = getDefaultElogInstance(url, **kwargs)
        self._screenshot = Screenshot(screenshot_directory)
        self.read = self._log.read

    def post(self, *args, Title=None, Author=None, **kwargs):
        """ """
        if not Author:
            Author = self.user
        return self._log.post(*args, Title=Title, Author=Author, **kwargs)

    def screenshot(self, message="", window=False, desktop=False, delay=3, **kwargs):
        filepath = self._screenshot.shoot()[0]
        kwargs.update({"attachments": [filepath]})
        self.post(message, **kwargs)


class Screenshot:
    def __init__(self, screenshot_directory="", **kwargs):
        self._screenshot_directory = screenshot_directory
        if not ("user" in kwargs.keys()):
            self.user = _getuser()
        else:
            self.user = kwargs["user"]

    def show_directory(self):

        p = subprocess.Popen(
            ["nautilus", self._screenshot_directory],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def shoot(self, message="", window=False, desktop=False, delay=3, **kwargs):
        cmd = ["gnome-screenshot"]
        if window:
            cmd.append("-w")
            cmd.append("--delay=%d" % delay)
        elif desktop:
            cmd.append("--delay=%d" % delay)
        else:
            cmd.append("-a")
        tim = datetime.datetime.now()
        fina = "%s-%s-%s_%s-%s-%s" % tim.timetuple()[:6]
        if "Author" in kwargs.keys():
            fina += "_%s" % user
        else:
            fina += "_%s" % self.user
        fina += ".png"
        filepath = os.path.join(self._screenshot_directory, fina)
        cmd.append("--file")
        cmd.append(filepath)
        p = subprocess.call(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return filepath, p
