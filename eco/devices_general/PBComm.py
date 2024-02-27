#!/usr/bin/env python
# *-----------------------------------------------------------------------*
# |                                                                       |
# |  Copyright (c) 2015 by Paul Scherrer Institute (http://www.psi.ch)    |
# |                                                                       |
# |              Author Thierry Zamofing (thierry.zamofing@psi.ch)        |
# *-----------------------------------------------------------------------*
"""
PBComm:
  SSH communication class with gpascii.
"""

# backup Motor[1].status
# backup Coord[x].Status
# backup Sys.Status
# ? -> $00000080

from __future__ import print_function

# import wx
import time
import paramiko, re, os

# WX3=(wx.VERSION[0]==3) #old version 3.x

import logging

_log = logging.getLogger(__name__)


def debug(*args):
    _log.debug(" ".join(map(str, args)))


class SSHComm:
    """Communicates with the Delta Tau gpascii programm wia SSH"""

    gpascii_ack = "\r\n\x06\r\n"
    gpascii_inp = "Input\r\n"

    def __init__(self):
        self.reqSet = set()
        # def __init__(self,args=None):
        # self.args=args
        self.defDictStack = [
            dict()
        ]  # definition dictionaties. stacked with OPEN and CLOSE commands

        pass

    def connect(self, host, password="deltatau", timeout=30.0):
        "start communication to gpascii by starting a SSH session (if needed and gpascii on the PowerBrick"
        p = host.rfind(":")
        if p >= 0:
            hostname = host[:p]
            port = int(host[p + 1 :])
        else:
            hostname = host
            port = 22
        p = hostname.rfind("@")
        if p >= 0:
            username = hostname[:p]
            hostname = hostname[p + 1 :]
        else:
            username = "root"

        self.client = client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        # client.set_missing_host_key_policy(paramiko.WarningPolicy())
        cfg = {
            "hostname": hostname,
            "port": port,
            "username": username,
            "password": password,
            "timeout": timeout,
        }

        try:
            with open(os.path.expanduser("~/.ssh/config")) as fh:
                ssh_config = paramiko.SSHConfig()
                ssh_config.parse(fh)
        except IOError as e:
            pass
        else:
            host_conf = ssh_config.lookup(hostname)
            for k, v in host_conf.items():
                if k == "hostname":
                    cfg[k] = v
                elif k == "user":
                    cfg["username"] = v
                elif k == "port":
                    cfg[k] = int(v)
                elif k == "identityfile":
                    cfg["key_filename"] = os.path.expanduser(v[0])
                # elif k == "stricthostkeychecking" and v == "no":
                #  client.set_missing_host_key_policy(paramiko.WarningPolicy())
                elif k == "requesttty":
                    self.get_pty = v in ("yes", "force")
                elif k == "gssapikeyexchange":
                    cfg["gss_auth"] = v == "yes"
                elif k == "gssapiauthentication":
                    cfg["gss_kex"] = v == "yes"
                elif k == "proxycommand":
                    cfg["sock"] = paramiko.ProxyCommand(v)

        client.connect(**cfg)
        self.gpascii()

    def gpascii(self):
        self.chan = (
            chan
        ) = self.client.invoke_shell()  # -> returns a paramiko.channel.Channel
        chan.settimeout(1.0)
        self.read_until("root@.*?# ")
        chan.send("stty -echo\n")  # dont't echo the send data
        chan.send("gpascii -2\n")
        try:
            self.read_until(".*?STDIN Open for ASCII Input")
        except TimeoutError as err:
            raise ValueError("GPASCII startup string not found")

        # chan.send('#1..8p\n')
        # print(self.read_until_endswith(SSHComm.gpascii_ack).rstrip(SSHComm.gpascii_ack))
        # print('done')

    def read_until(self, regex, timeout=1.0):
        "SSH wait, for regex in buffer up to `timeout` seconds,"
        chan = self.chan
        wait_re = re.compile(regex)
        t0 = time.time()
        buf = ""

        while True:
            if chan.recv_ready():
                t0 = time.time()  # reset timeout
                s = chan.recv(4096).decode()
                debug(repr(s))
                buf += s
                m = wait_re.search(buf)
                if m:
                    return (buf, m.span())
            elif (time.time() - t0) > timeout:
                raise TimeoutError(repr(buf))
            else:
                time.sleep(0.001)

    def read_until_endswith(self, txt, timeout=1.0):
        "SSH wait, up until received data ends with text up to `timeout` seconds"
        chan = self.chan
        t0 = time.time()
        buf = ""
        while True:
            if chan.recv_ready():
                t0 = time.time()  # reset timeout
                s = chan.recv(4096).decode()
                debug(repr(s))
                buf += s
                if buf.endswith(txt):
                    return buf
            elif (time.time() - t0) > timeout:
                raise TimeoutError(repr(buf))
            else:
                time.sleep(0.001)

    def iawrite(self, msg):
        """interactive write. if 1 is returned, nothing is done"""
        m = re.match("\s*(define|open|close)", msg, re.IGNORECASE)
        if m:
            kw = m.group(1).lower()
            if kw == "open":
                self.defDictStack.append(dict())
                print(self.defDictStack)
            elif kw == "close":
                if len(self.defDictStack) > 1:
                    del self.defDictStack[-1]
                else:
                    print("close stack error")
                print(self.defDictStack)
            elif kw == "define":
                m = re.match("\s*define\s*\(([^)]*)", msg, re.IGNORECASE)
                if m:
                    args = eval("dict(" + m.group(1) + ")")
                    self.defDictStack[-1].update(args)
                print(self.defDictStack)
                return 1
        for defDic in reversed(self.defDictStack):
            for k, v in defDic.items():
                msg = re.sub(
                    r"(\W|^)" + k + r"(?=\W|$)", r"\g<1>" + str(v), msg
                )  # define must be separated with non-alphanumeric character [^a-zA-Z0-9_], or at begin/end

        if False:
            # self.args.dryrun:
            # print(msg)
            return 1

        elif hasattr(self, "chan"):
            self.chan.send(msg + "\n")
        else:
            print("nothing done! no opened connection.")
            return 1
        return None


if __name__ == "__main__":
    import argparse  # since python 2.7

    def ParseArgs(required=True):
        parser = argparse.ArgumentParser(
            description=__doc__, formatter_class=argparse.RawTextHelpFormatter
        )
        parser.add_argument(
            "--host",
            default="SARES20-CPPM-EXP1",
            help="the hostname (default=%(default)s)",
        )
        return parser.parse_args()

    # --- main code ---
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname).1s:%(module)s:%(lineno)d:%(funcName)s:%(message)s ",
    )
    args = ParseArgs()

    gpascii = SSHComm()
    gpascii.connect(args.host)
    chan = gpascii.chan
    cmd = {
        b"EncTable[1].PrevEnc\n",
        b"EncTable[3].PrevEnc\n",
        b"#9;p\n",
        b"Motor[5].pos\n",
        b"EncTable[7].PrevEnc\n",
        b"Motor[4].idCmd\n",
        b"Motor[2].Ctrl\n",
        b"Motor[2].pos\n",
        b"EncTable[4].PrevEnc\n",
        b"Motor[1].Ctrl\n",
        b"#8?\n",
        b"#13;p\n",
        b"EncTable[2].PrevEnc\n",
        b"Motor[5].idCmd\n",
        b"#5?\n",
        b"EncTable[5].PrevEnc\n",
        b"Motor[2].idCmd\n",
        b"#1;p\n",
        b"#2;p\n",
        b"#7?\n",
        b"Motor[4].Ctrl\n",
        b"Motor[8].idCmd\n",
        b"#12;p\n",
        b"Motor[6].idCmd\n",
        b"#14;p\n",
        b"#5;p\n",
        b"Motor[5].Ctrl\n",
        b"#6?\n",
        b"Motor[4].pos\n",
        b"Motor[1].idCmd\n",
        b"Motor[3].pos\n",
        b"#10;p\n",
        b"#15;p\n",
        b"#4?\n",
        b"#6;p\n",
        b"Motor[3].idCmd\n",
        b"#8;p\n",
        b"Motor[7].Ctrl\n",
        b"#7;p\n",
        b"Motor[8].Ctrl\n",
        b"Motor[6].pos\n",
        b"#3;p\n",
        b"?\n",
        b"EncTable[8].PrevEnc\n",
        b"#11;p\n",
        b"#1?\n",
        b"Motor[8].pos\n",
        b"Motor[7].pos\n",
        b"#3?\n",
        b"EncTable[6].PrevEnc\n",
        b"#2?\n",
        b"#16;p\n",
        b"#4;p\n",
        b"Motor[7].idCmd\n",
        b"Motor[3].Ctrl\n",
        b"Motor[1].pos\n",
        b"Motor[6].Ctrl\n",
    }
    c = ""
    for k in cmd:
        c += k.decode()
    fn = "/tmp/PBComm.log"
    fh = open(fn, "w")
    fh.write(c + "\n\n\n***\n")
    for i in range(100):
        chan.send(c)
        res = gpascii.read_until_endswith(SSHComm.gpascii_ack)
        fh.write(res)
        print(i, len(res))
    print(fn + " generated.")
