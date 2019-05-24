#!/usr/bin/env python

from eco import ecocnf


def main():
    import argparse

    parser = argparse.ArgumentParser(description="eco startup utility")

    parser.add_argument(
        "-s",
        "--scope",
        type=str,
        default=None,
        help="scope name, usually instrument or beamline",
    )
    parser.add_argument(
        "-a",
        "--scopes_available",
        action="store_true",
        default=False,
        help="print available scopes.",
    )
    parser.add_argument(
        "-l", "--lazy", action="store_true", default=False, help="lazy initialisation"
    )
    parser.add_argument(
        "--shell", type=bool, default=True, help="open eco in ipython shell"
    )
    parser.add_argument(
        "--pylab", type=bool, default=True, help="open ipython shell in pylab mode"
    )

    arguments = parser.parse_args()

    scope = arguments.scope
    # scope = 'bernina'

    if arguments.scopes_available:
        print("{:<15s}{:<15s}{:<15s}".format("module", "name", "facility"))
        for ts in ecocnf.scopes:
            print(
                " {:<14s} {:<14s} {:<14s}".format(
                    ts["module"], ts["name"], ts["facility"]
                )
            )

        return

    print(
        "                       ___ _______\n                      / -_) __/ _ \ \n Experiment Control   \__/\__/\___/ \n\n"
    )

    if scope:
        # import importlib
        # eco = importlib.import_module('eco')
        # mdl = importlib.import_module(scope,package=eco)
        # mdl = importlib.import_module('eco.bernina')
        if arguments.lazy:
            ecocnf.startup_lazy = True
        exec(f"import eco.{scope} as {scope}")
        # exec(f'{scope}.init(lazy=ecocnf.startup_lazy)')
        exec(f"from eco.{scope} import *")
        # is there an __all__?  if so respect it
        # if "__all__" in mdl.__dict__:
        #    names = mdl.__dict__["__all__"]
        # else:
        # otherwise we import all names that don't begin with _
        #    names = [x for x in mdl.__dict__ if not x.startswith("_")]
        # now drag them in
        # globals().update({k: getattr(mdl, k) for k in names})

    if arguments.shell:
        if arguments.pylab:
            import matplotlib.pyplot as plt
            from IPython.terminal.embed import InteractiveShellEmbed

            shell = InteractiveShellEmbed()
            shell.enable_matplotlib()
            shell()
        else:
            from IPython import start_ipython

            start_ipython()


if __name__ == "__main__":
    main()
