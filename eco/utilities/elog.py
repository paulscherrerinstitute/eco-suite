from pathlib import Path
import elog as _elog_ha
from getpass import getuser as _getuser
from getpass import getpass as _getpass
import os, datetime, subprocess
from markdown import markdown
import urllib3

urllib3.disable_warnings()


######################
class ElogsMultiplexer:
    def __init__(self, *args):
        self.elogs = args

    def post(self, *args, **kwargs):
        mids = []
        for elog in self.elogs:
            mids.append(elog.post(*args, **kwargs))
        return mids


##########################


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

    def post(
        self,
        *args,
        text_encoding="markdown",
        markdown_extensions=["fenced_code"],
        tags=[],
        Title=None,
        Author=None,
        **kwargs,
    ):
        """ """

        message = ""
        file_paths = []

        for targ in args:
            if not (isinstance(targ, str) or isinstance(targ, Path)):
                raise Exception(
                    "Log messages should be of type string or pathlib.Path!"
                )

            if isinstance(targ, Path):
                if Path(targ).expanduser().exists():
                    print("file exists")
                    file_paths.append(targ.as_posix())
                    if targ.suffix[1:] in ["jpg", "png"]:
                        if text_encoding in ["markdown", "html"]:
                            message += f'<p><img alt="" src="temporarypath-attachment_{len(file_paths)-1}" /></p>'
            else:
                targ = str(targ)
                if text_encoding == "markdown":
                    message += markdown(targ, extensions=markdown_extensions)
                    Encoding = "html"
                elif text_encoding == "html":
                    Encoding = "html"
                else:
                    message += targ + "\n"
                    Encoding = "plain"

        if not Author:
            Author = self.user

        if file_paths:
            attachments = file_paths
        else:
            attachments = None
        mid = self._log.post(
            message,
            attachments=attachments,
            Title=Title,
            Author=Author,
            Encoding=Encoding,
            **kwargs,
        )

        if file_paths:
            pm, patt, pa = self._log.read(mid)
            for ntpa, tpa in enumerate(pa):
                filename = "".join(Path(tpa).parts[-1].split("_")[2:])
                print(filename)
                Nocc = pm.count(f"temporarypath-attachment_{ntpa}")
                print(Nocc)
                if Nocc:
                    pm = pm.replace(f"temporarypath-attachment_{ntpa}", tpa)
            self._log.post(pm, msg_id=mid)

        return mid

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
