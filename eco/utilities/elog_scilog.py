from functools import lru_cache
from markdown import markdown
from scilog import SciLog, LogbookMessage
from getpass import getuser as _getuser
from getpass import getpass as _getpass
import os, datetime, subprocess, html, uuid
import urllib3
from pathlib import Path

from eco.elements.assembly import Assembly
from eco.utilities.secrets_gopass import get_gopass_password

urllib3.disable_warnings()


_DICT_TREE_CSS = """
body { font-family: -apple-system, Helvetica, Arial, sans-serif; font-size: 13px; margin: 8px; }
details { margin-left: 14px; }
summary { cursor: pointer; font-weight: 600; }
summary:hover { color: #0366d6; }
.leaf { margin-left: 14px; font-family: Menlo, Consolas, monospace; }
.leaf .key { font-weight: 600; }
.leaf .val { color: #22863a; }
"""


def _render_dict_node(obj, key=None):
    """Recursively render a value as nested <details>/<summary> HTML (no JS)."""
    if isinstance(obj, dict):
        label = html.escape(str(key)) if key is not None else "{ }"
        inner = "".join(_render_dict_node(v, k) for k, v in obj.items()) or "<div class='leaf'><em>empty</em></div>"
        return f"<details open><summary>{label}</summary>{inner}</details>"
    elif isinstance(obj, (list, tuple)):
        label = f"{html.escape(str(key))} [{len(obj)}]" if key is not None else f"[{len(obj)}]"
        inner = "".join(_render_dict_node(v, i) for i, v in enumerate(obj)) or "<div class='leaf'><em>empty</em></div>"
        return f"<details><summary>{label}</summary>{inner}</details>"
    else:
        keytxt = f"<span class='key'>{html.escape(str(key))}:</span> " if key is not None else ""
        return f"<div class='leaf'>{keytxt}<span class='val'>{html.escape(repr(obj))}</span></div>"


def dict_to_html(data, title="data"):
    """Render a nested dict/list structure as a self-contained, collapsible HTML
    page (plain HTML5 <details>/<summary>, no JavaScript dependency)."""
    body = _render_dict_node(data, key=title)
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}"
        f"</title><style>{_DICT_TREE_CSS}</style></head><body>{body}</body></html>"
    )


def write_dict_html(data, filepath=None, title="data", directory=""):
    """Write dict_to_html(data) to disk and return the Path to it.

    If filepath is not given, a file named '<title>_<uuid>.html' is created in
    `directory` (or the current directory)."""
    if filepath is None:
        fname = f"{title}_{uuid.uuid4().hex[:8]}.html"
        filepath = Path(directory) / fname if directory else Path(fname)
    filepath = Path(filepath)
    filepath.write_text(dict_to_html(data, title=title))
    return filepath


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
        _pw = get_gopass_password("elog/scilog-password")
        if _pw is None:
            try:
                with open(os.path.join(home, ".scilog_psi"), "r") as f:
                    _pw = f.read().strip()
            except:
                print(f"Enter scilog password for user: {user}")
                _pw = _getpass()
        kwargs.update(dict(password=_pw))
    log = SciLog(url, options := {"username": user, "password": kwargs["password"]})
    if pgroup:
        lbs = log.get_logbooks(ownerGroup=pgroup)
        if len(lbs) > 1:
            print(f"Found more than one elog for user group {pgroup}")
            for lb in lbs:
                creater = lb.createdBy
                if creater == "scilog-admin@psi.ch":
                    log.select_logbook(lb)
                    print(f"Choosing default logbook created by 'scilog-admin@psi.ch'")
        else:
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
        pgroups=None,
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

        return self._log.send_logbook_message(msg)

    def screenshot(self, message="", window=False, desktop=False, delay=3, **kwargs):
        filepath = self._screenshot.shoot()[0]
        kwargs.update({"attachments": [filepath]})
        self.post(message, **kwargs)

    def post_dict(self, data, text="", title="data", **kwargs):
        """Post `text` together with `data` (a nested dict/list) rendered as a
        collapsible HTML tree, attached as a file. SciLog itself only renders
        plain formatted text/images inline, so the tree is not visible in the
        message body -- it appears as a file link that opens the interactive
        (expand/collapse) tree in a new browser tab.
        """
        filepath = write_dict_html(
            data, title=title, directory=self._screenshot._screenshot_directory
        )
        try:
            return self.post(text, filepath, **kwargs)
        finally:
            filepath.unlink(missing_ok=True)


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
