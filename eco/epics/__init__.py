from eco import ecocnf


def get_from_archive(Obj, attribute_name="pvname"):
    def get_archiver_time_range(self, start=None, end=None, plot=True):
        """Try to retrieve data within timerange from archiver. A time delta from now is assumed if end time is missing."""
        channelname = self.__dict__[attribute_name]
        return ecocnf.archiver.get_data_time_range(
            channels=[channelname], start=start, end=end, plot=plot
        )

    Obj.get_archiver_time_range = get_archiver_time_range
    return Obj


