import subprocess, os


def get_strip_chart_function():
    return strip_chart


def strip_chart(*args, **kwargs):
    """Usage: Arguments represent channels in the strip_chart config command line argument.
    Alternatively arguments can be detectors or adjustables, from which _all_ channels are determined
    """
    channels = list(args)
    cmd = ["strip_chart"]
    cmd += ['-config="' + str(channels) + '"']
    cmd += ["-start"]
    line = " ".join(cmd)
    print(f"Starting following commandline silently:\n" + line)
    with open(os.devnull, "w") as FNULL:
        subprocess.Popen(line, shell=True, stdout=FNULL, stderr=subprocess.STDOUT)
