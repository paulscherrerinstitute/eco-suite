from functools import lru_cache
from markdown import markdown
from scilog import SciLog, LogbookMessage
from getpass import getuser as _getuser
from getpass import getpass as _getpass
import os, datetime, subprocess
import urllib3
from pathlib import Path

from eco.elements.assembly import Assembly

urllib3.disable_warnings()


def getDefaultElogInstance(
    url="https://scilog.psi.ch/api/v1",
    user="swissfelaramis-bernina@psi.ch",
    pgroup=None,
    **kwargs,
):
    home = str(Path.home())
    if not user:
        user = _getuser()

    if not ("password" in kwargs.keys()):
        try:
            with open(os.path.join(home, ".scilog_psi"), "r") as f:
                _pw = f.read().strip()
        except:
            print("Enter scilog password for user: %s" % kwargs["user"])
            _pw = _getpass()
        kwargs.update(dict(password=_pw))
    log = SciLog(url, options := {"username": user, "password": kwargs["password"]})
    if pgroup:
        lbs = log.get_logbooks(ownerGroup=pgroup)
        if len(lbs) > 1:
            raise Exception(f"Found more than one elog for user group {pgroup}")
        log.select_logbook(lbs[0])
    return log, user


class Elog(Assembly):
    def __init__(
        self,
        url="https://scilog.psi.ch/api/v1",
        pgroup_adj=None,
        screenshot_directory="",
        name="scilog",
        **kwargs,
    ):
        super().__init__(name=name)
        self.scilog_url = url
        self._append(pgroup_adj, name="pgroup")
        dummy, self.user = getDefaultElogInstance(
            url, pgroup=pgroup_adj.get_current_value(), **kwargs
        )
        self.__class__._log = property(
            lambda dum: self._get_scilog_dynamically(
                self.scilog_url, self.pgroup.get_current_value()
            )
        )
        self._screenshot = Screenshot(screenshot_directory)
        # self.read = self._log.read

    @lru_cache
    def _get_scilog_dynamically(self, url, pgroup):
        log, user = getDefaultElogInstance(url, pgroup=pgroup)
        self.user = user
        return log

    def post(
        self,
        *args,
        tags=[],
        text_encoding="markdown",
        markdown_extensions=["fenced_code"],
        **kwargs,
    ):
        """args can be text or pathlibPath instances (for files to be uploaded)"""
        msg = LogbookMessage()
        for targ in args:
            if not (isinstance(targ, str) or isinstance(targ, Path)):
                raise Exception("Log messages should be of type string!")

            if isinstance(targ, Path):
                if Path(targ).expanduser().exists():
                    print("file exists")
                    msg.add_file(targ.as_posix())
            else:
                targ = str(targ)
                if text_encoding == "markdown":
                    msg.add_text(markdown(targ, extensions=markdown_extensions))
                elif text_encoding == "html":
                    msg.add_text(targ)
                else:
                    msg.add_text(targ)

        for tag in tags:
            msg.add_tag(tag)

        self._log.send_logbook_message(msg)

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
