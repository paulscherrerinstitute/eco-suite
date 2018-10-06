from epics import caput, caget


class laser_shutter:
    def __init__(self, Id):
        self.Id = Id

    def __repr__(self):
        return self.get_status()

    def get_status(self):
        Id = self.Id
        status = caget(Id + ":FrontUnivOut5-Ena-SP")
        if status == 0:
            return "open"
        elif status == 1:
            return "close"
        else:
            return "unknown"

    def open(self):
        caput(self.Id + ":FrontUnivOut5_SOURCE", 3)
        caput(self.Id + ":FrontUnivOut5_SOURCE2", 4)
        caput(self.Id + ":FrontUnivOut5-Ena-SP", 0)

    def close(self):
        caput(self.Id + ":FrontUnivOut5_SOURCE", 3)
        caput(self.Id + ":FrontUnivOut5_SOURCE2", 4)
        caput(self.Id + ":FrontUnivOut5-Ena-SP", 1)
