from bsread import Source
from bsread.h5 import receive, process_message_compact
from bsread.avail import dispatcher
import zmq
import mflow
import os
import data_api as api
import datetime
from threading import Thread
from pathlib import Path
from .utilities import Acquisition


class BStools:
    def __init__(
        self,
        default_channel_list={"listname": []},
        default_file_path="%s",
        elog=None,
        name=None,
    ):
        self._default_file_path = default_file_path
        self._default_channel_list = default_channel_list
        self._elog = elog
        self.name = name

    def avail(self, *args, **kwargs):
        return dispatcher.get_current_channels(*args, **kwargs)

    def check_channel_list(self, printResult=True, printOnlineChannels=False):
        all_available = set([i["name"] for i in self.avail()])
        status = {}
        for listname in self._default_channel_list.keys():
            tch = set(self._default_channel_list[listname])
            status[listname] = {}
            status[listname]["online"] = tch.intersection(all_available)
            status[listname]["offline"] = tch.difference(all_available)
        if printResult:
            for listname in status.keys():
                if printOnlineChannels:
                    print("#### Online Channels in {} ####".format(listname))
                    print("\n".join(status[listname]["online"]))
                    print("\n")
                print("#### Offline Channels in {} ####".format(listname))
                print("\n".join(status[listname]["offline"]))
        else:
            return status

    def cleanup_channel_list(self, listname):
        status = self.check_channel_list(printResult=False)
        self._default_channel_list[listname] = list(
            set(self._default_channel_list[listname]).difference(
                set(status[listname]["offline"])
            )
        )
        print("#### Temporarily removed Offline Channels in {} ####".format(listname))
        print("\n".join(status[listname]["offline"]))
        print(
            "NB: The channels will be back after restart if they originate from a config file."
        )

    def h5(
        self,
        fina=None,
        channel_list=None,
        N_pulses=None,
        default_path=True,
        queue_size=100,
        compact_format=False,
    ):

        if os.path.isfile(fina):
            print("!!! File %s already exists, would you like to delete it?" % fina)
            if input("(y/n)") == "y":
                print("Deleting %s ." % fina)
                os.remove(fina)
            else:
                return

        path_as_path = Path(fina)

        if not path_as_path.parent.exists():
            path_as_path.parent.mkdir()

        if not channel_list:
            print("No channels specified, using all lists instead.")
            channel_list = []
            for tlist in self._default_channel_list.values():
                channel_list.extend(tlist)
            print(channel_list)
        if compact_format:
            message_processor = process_message_compact
        else:
            message_processor = None

        source = dispatcher.request_stream(channel_list)
        mode = zmq.SUB
        try:
            print(f"message proc is {message_processor}")
            receive(
                source,
                fina,
                queue_size=queue_size,
                mode=mode,
                n_messages=N_pulses,
                message_processor=message_processor,
            )
        except KeyboardInterrupt:
            # KeyboardInterrupt is thrown if the receiving is terminated via ctrl+c
            # As we don't want to see a stacktrace then catch this exception
            pass
        finally:
            print("Closing stream")
            dispatcher.remove_stream(source)

    def db(
        self,
        channel_list=None,
        start_time_delta=dict(),
        end_time_delta=dict(),
        default_path=True,
    ):
        if not channel_list:
            print(
                "No channels specified, using default list '%s' instead."
                % list(self._default_channel_list.keys())[0]
            )
            channel_list = self._default_channel_list[
                list(self._default_channel_list.keys())[0]
            ]
        now = datetime.datetime.now()
        end = now - datetime.timedelta(**end_time_delta)
        start = end - datetime.timedelta(**start_time_delta)
        return api.get_data(channels=channel_list, start=start, end=end)

    def h5_db(
        self,
        fina,
        channel_list=None,
        start_time_delta=dict(),
        end_time_delta=dict(),
        default_path=True,
    ):
        data = self.db(
            channel_list=None,
            start_time_delta=start_time_delta,
            end_time_delta=end_time_delta,
            default_path=True,
        )
        if default_path:
            fina = self._default_file_path % fina

        if os.path.isfile(fina):
            print("!!! File %s already exists, would you like to delete it?" % fina)
            if input("(y/n)") == "y":
                print("Deleting %s ." % fina)
                os.remove(fina)
            else:
                return

        data.to_hdf(fina, "/data")

    def acquire(self, file_name=None, Npulses=100):
        file_name += ".h5"

        if self._default_file_path:
            file_name = self._default_file_path % file_name

        def acquire():
            self.h5(fina=file_name, N_pulses=Npulses)

        return Acquisition(
            acquire=acquire,
            acquisition_kwargs={"file_names": [file_name], "Npulses": Npulses},
            hold=False,
        )

    def wait_done(self):
        self.check_running()
        self.check_still_running()
