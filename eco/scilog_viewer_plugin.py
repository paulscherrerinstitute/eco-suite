"""eco's parts of the SciLog viewer (the scilog-viewer package), loaded by
its server through ``--plugin eco.scilog_viewer_plugin`` (eco.scilog_app.start
passes it): the Bernina beamline account, the shared console account, eco's
token file and settings, and the test logbook writable until a user says
otherwise. See scilog_viewer.load_plugin."""

from pathlib import Path

#: writable until a user switches on others (p22891, the test logbook)
TEST_LOGBOOK = "68afe9fe6f90f96e7370f9b3"


def setup(client, server):
    from eco import logs_scilog

    client.FUNCTIONAL_USER = logs_scilog.FUNCTIONAL_USER
    client.SHARED_ACCOUNTS = tuple(dict.fromkeys((*client.SHARED_ACCOUNTS, *logs_scilog.SHARED_ACCOUNTS)))

    def functional_login(url=client.URL, session=None, timeout=30):
        password = logs_scilog._functional_password()  # gopass elog/scilog-password or ~/.scilog_psi
        if not password:
            return None
        return client.login(logs_scilog.FUNCTIONAL_USER, password, url=url, session=session, timeout=timeout)

    functional_login.available = logs_scilog.functional_available
    client.functional_login = functional_login
    # one token for the viewer and %ei: the one kept in ~/.eco/scilog_token
    client.TOKEN_PATH = logs_scilog.TOKEN_PATH
    client.TOKEN_HELP = logs_scilog.TOKEN_HELP
    server.CONFIG_PATH = Path.home() / ".eco" / "scilog_app.json"
    server.DEFAULT_WRITE_LOGBOOKS = [TEST_LOGBOOK]
