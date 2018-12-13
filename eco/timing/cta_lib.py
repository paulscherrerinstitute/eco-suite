from epics import PV
import numpy
import logging
import time
import threading

"""
The complex triggering application (CTA) is an application which allows the
user to inject a user defined sequence of events in a subtree of the timing
tree.
The CTA runs on an IOC. There are two ways to program and control it.
The first is a GUI and the second is this python library.
"""


class CtaLib:
    def __init__(self, device, sequence=0, log_level="warning"):
        """
    Constructor

    Arguments
    device: device name (e.g. SAR-CCTA-ESA)
    sequence: sequence number (default = 0)
    log_level: critical, error, warning, info, debug
    """
        self.event_code_range_base = 200
        self.num_of_event_codes = 20

        # setup logging
        numeric_level = getattr(logging, log_level.upper(), None)
        if not isinstance(numeric_level, int):
            raise ValueError("Invalid log_level: %s" % log_level)
        logging.basicConfig(
            level=numeric_level, format="%(asctime)s | %(levelname)8s | %(message)s"
        )
        logging.info("__init__() is running (device=" + device + ")")

        # create threading event
        self.event = threading.Event()

        # create connection housekeeper
        self.num_connected = 0

        # create attributes for callback support
        self._status_callbacks = list()
        self._series_callbacks = list()

        # create pv objects
        self.pvs = dict()

        self.num_of_pvs = 26

        pv_name = device + ":SerMaxLen"
        self.pvs["SerMaxLen"] = PV(
            pv_name, connection_callback=self.connection_callback
        )

        pv_name = device + ":seq" + str(sequence) + "Ctrl-Length-I"
        self.pvs["Ctrl-Length-I"] = PV(
            pv_name, connection_callback=self.connection_callback
        )
        pv_name = device + ":seq" + str(sequence) + "Ctrl-Cycles-I"
        self.pvs["Ctrl-Cycles-I"] = PV(
            pv_name, connection_callback=self.connection_callback
        )
        pv_name = device + ":seq" + str(sequence) + "Ctrl-Start-I"
        self.pvs["Ctrl-Start-I"] = PV(
            pv_name, connection_callback=self.connection_callback
        )
        pv_name = device + ":seq" + str(sequence) + "Ctrl-Stop-I"
        self.pvs["Ctrl-Stop-I"] = PV(
            pv_name, connection_callback=self.connection_callback
        )
        pv_name = device + ":seq" + str(sequence) + "Ctrl-IsRunning-O"
        self.pvs["Ctrl-IsRunning-O"] = PV(
            pv_name,
            callback=self._status_callback,
            connection_callback=self.connection_callback,
        )

        self.pvs["Data-I"] = list()
        for i in range(0, self.num_of_event_codes):
            pv_name = device + ":seq" + str(sequence) + "Ser" + str(i) + "-Data-I"
            self.pvs["Data-I"].append(
                PV(
                    pv_name,
                    callback=self._series_callback,
                    connection_callback=self.connection_callback,
                )
            )

        # wait for the connections to be established
        rv = self.event.wait(timeout=5.0)
        if not rv:
            raise RuntimeError("Some PV(s) is/are not connected")
        time.sleep(1)  # NOTE02

        # logging
        for i in range(0, self.num_of_event_codes):
            logging.debug("NORD of " + str(i) + ":" + str(self.pvs["Data-I"][i]))
        logging.info("__init__() is done")

    def __del__(self):
        """
    Deconstructor
    """

        logging.info("__del__() is running")

        del self.pvs

        logging.info("__del__() is done")

    def download(self, seq):
        """
    Download a sequence to the IOC

    Arguments
    seq: The sequence to be downloaded to the IOC.
         seq is a dictionary where each key value pair represents a series.
         A series is a list of 0's and 1's which defines, if the corresponding event code
         is send in the corresponding machine pulse.
         The key is an integer and represents the event code.
         The value is the series.
         If a certain event code is not send in the sequence, it may or may not
         not be present in the dictionary.
         Example:
           seq = {200: [1, 0], 201: [1, 1]}
           =>
           machine pulse     x: event code 200 is send
           machine pulse x + 1: event code 200 and 201 are send
    """

        logging.info("download() is running")

        # check the sequence
        self.check_sequence(seq)

        # fill empty series
        seq = self.fill_empty_series(seq)

        # logging
        logging.debug("download() downloads: " + str(seq))

        # check connections
        rv = self.event.wait(timeout=5.0)
        if not rv:
            raise RuntimeError("Some PV(s) is/are not connected")

        # downloading seq to pvs
        for i in range(0, self.num_of_event_codes):
            self.pvs["Data-I"][i].put(
                numpy.array(seq[self.event_code_range_base + i]), wait=True
            )

        # set length
        self.pvs["Ctrl-Length-I"].put(len(seq[self.event_code_range_base]), wait=True)

        logging.info("download() is done")

    def upload(self):
        """
    Upload a sequence from the IOC

    Return
    seq: The sequence uploaded from the IOC.
         Refer to the download method for a definition of seq.
    """

        logging.info("upload() is running")

        # check connections
        rv = self.event.wait(timeout=5.0)
        if not rv:
            raise RuntimeError("Some PV(s) is/are not connected")

        # upload
        seq = {}
        for i in range(0, self.num_of_event_codes):
            logging.debug("NORD of " + str(i) + ":" + str(self.pvs["Data-I"][i]))
            seq[self.event_code_range_base + i] = numpy.atleast_1d(
                self.pvs["Data-I"][i].get()
            ).tolist()

        # logging
        logging.debug("upload() uploaded: " + str(seq))

        # check the sequence
        self.check_sequence(seq)

        logging.info("upload() is done")

        return seq

    def start(self, repetitions):
        """
    Start CTA

    Arguments
    repetitions: 0 = forever, x = x repetitions
    """

        logging.info("start() is running (repetitions=" + str(repetitions) + ")")

        # check connections
        rv = self.event.wait(timeout=5.0)
        if not rv:
            raise RuntimeError("Some PV(s) is/are not connected")

        # start
        self.pvs["Ctrl-Cycles-I"].put(repetitions, wait=True)
        self.pvs["Ctrl-Start-I"].put(1, wait=True)

        time.sleep(3)  # => NOTE01

        logging.info("start() is done")

    def stop(self):
        """
    Stop CTA

    """

        logging.info("stop() is running")

        # check connections
        rv = self.event.wait(timeout=5.0)
        if not rv:
            raise RuntimeError("Some PV(s) is/are not connected")

        self.pvs["Ctrl-Stop-I"].put(1, wait=True)

        # stop
        time.sleep(3)  # => NOTE01

        logging.info("stop() is done")

    def is_running(self):
        """
    Check if CTA is running

    Return
    True if CTA is running, False otherwise
    """

        logging.info("is_running() is running")

        # check connections
        rv = self.event.wait(timeout=5.0)
        if not rv:
            raise RuntimeError("Some PV(s) is/are not connected")

        # get status
        if self.pvs["Ctrl-IsRunning-O"].get() != 0:
            rv = True
        else:
            rv = False

        logging.info("is_running() is done")

        return rv

    def register_status_callback(self, callback):
        """
    This function can be used to register a callback function which is
    called if the status of the sequence controller changed.

    The following arguments will be passed to the callback function:
      value: 1 if sequence is running, 0 otherwise

    Keep your callback function short.

    Arguments
    callback: Function to be called.
    """
        self._status_callbacks.append(callback)

    def register_series_callback(self, callback):
        """
    This function can be used to register a callback function which is
    called if a series of the sequence on the IOC has changed.

    Refer to the download method for a definition of a sequence.

    The following arguments will be passed to the callback function:
      sequence: sequence containing the series which has changed

    Keep your callback function short.

    Arguments
    callback: Function to be called
    """
        self._series_callbacks.append(callback)

    def check_sequence(self, seq):
        """
    Check if a sequence is valid

    Arguments
    seq: The sequence to be checked.
         A RunTimeError exception is thrown if the sequence is not valid.
         Refer to the download method for a definition of seq.
    """

        logging.info("check_sequence() is running")

        # check that seq has correct types and at least one series
        if type(seq) != type(dict()):
            raise RuntimeError("seq arg is not a dictionary")
        if len(list(seq)) == 0:
            raise RuntimeError("dictionary seq is empty")
        for key, series in seq.items():
            if type(key) != type(int()):
                raise RuntimeError(
                    "dictionary contains key value pair where key is" " not an int"
                )
            if type(series) != type(list()):
                raise RuntimeError(
                    "dictionary contains key value pair where value is" " not a list"
                )
            for i in range(len(series)):
                if series[i] != 0 and series[i] != 1:
                    raise RuntimeError(
                        "dictionary contains key value pair where value is"
                        " is a list with at least one element which is not 0 or 1"
                    )

        # check that all series have same length
        length = len(seq[list(seq)[0]])
        for key, series in seq.items():
            if len(seq[key]) != length:
                raise RuntimeError(
                    "dictionary contains key value pair where at least"
                    " two values are lists with different length"
                )

        # check that series are not too long
        length = len(seq[list(seq)[0]])
        if length > self.pvs["SerMaxLen"].get():
            raise RuntimeError(
                "dictionary contains key value pair where the values "
                "are lists with too many elements"
            )

        logging.info("check_sequence() is done")

    def fill_empty_series(self, seq):
        """
    Fill the sequence such that all events are described

    Arguments
    seq: A sequence where some events might not be defined.

    Return
    seq: The sequence with all events defined.

    Refer to the download method for a definition of seq.
    """

        logging.info("fill_empty_series() is running")

        length = len(seq[list(seq)[0]])
        for i in range(self.event_code_range_base, 220):
            if i not in seq:
                seq[i] = [0] * length

        logging.info("fill_empty_series() is done")

        return seq

    def print(self, seq):
        """
    Print the sequence to std output

    Arguments
    seq: The sequence to be printed.
         Refer to the download method for a definition of seq.
    """

        logging.info("print() is running")
        logging.debug("print() prints: " + str(seq))

        # check the sequence
        self.check_sequence(seq)

        length = len(seq[list(seq)[0]])

        print("      | <---------------- event -------------->")
        print("      | 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2")
        print("      | 0 0 0 0 0 0 0 0 0 0 1 1 1 1 1 1 1 1 1 1")
        print("pulse | 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9")
        print("-----------------------------------------------")
        for i in range(length):
            print(repr(i).rjust(5), "|", end="")
            for key, series in seq.items():
                print(repr(series[i]).rjust(2), end="")
            print()

        logging.info("print() is done")

    def _status_callback(self, value=None, **kw):
        """
    Callback function which is called when the status PV changes the value.
    It is used to call user callback functions which registered for
    for this event.
    """
        logging.info("_status_callback() is running (value=" + str(value) + ")")

        logging.info("calling status callbacks next")
        for cb in self._status_callbacks:
            cb(value)
        logging.info("calling status callbacks done")

    def _series_callback(self, pvname=None, value=None, **kw):
        """
    Callback function which is called when one of the PVs holding a series of
    the sequence changes the value. It is used to call user callback functions which
    registered for this event.
    """
        logging.info(
            "_series_callback() is running (pv=" + pvname + ", value=" + str(value)
        )

        event_number = self.event_code_range_base

        # determine event number from pvname
        for idx, pv in enumerate(self.pvs["Data-I"]):
            if pv.pvname == pvname:
                event_number = self.event_code_range_base + idx
                break

        # create sequence
        seq = {}
        seq[event_number] = numpy.atleast_1d(value).tolist()

        # call callbacks
        logging.info("calling sequence callbacks next")
        for cb in self._series_callbacks:
            cb(seq)
        logging.info("calling sequence callbacks done")

        logging.info("_series_callback() is done")

    def connection_callback(self, pvname=None, conn=None, **kw):
        """
    Callback function used internally to do connection status housekeeping

    Arguments
    pvname: name of PV for which the callbak is called
    conn: status of the connection
    """

        logging.info(
            "connection_callback() is running (pvname="
            + pvname
            + ", conn="
            + repr(conn)
            + ", thread_id="
            + str(threading.get_ident())
            + ")"
        )

        # do connection housekeeping
        if conn:
            self.num_connected += 1
        else:
            self.num_connected -= 1
        logging.debug("num_connected=" + str(self.num_connected))

        # signal to other thread
        if self.num_connected == self.num_of_pvs:
            self.event.set()
        else:
            self.event.clear()

        logging.info("connection_callback() is done")


# NOTE 01
# This sleep is needed for unknown reasons.
# If it is not there, we get the following error if the lib object
# goes out of scope after start():
#   FATAL: exception not rethrown
#   CA client library tcp receive thread terminating due to a non-standard C++ exception
#   Aborted
#
# NOTE 02
# This sleep is needed for the initial ca communication to be completed.
# If it is not there and the upload is called right after object creation,
# the number of elements in the PV has not arrived in python for all PVs.
# This leads to a fail of check_sequence().
