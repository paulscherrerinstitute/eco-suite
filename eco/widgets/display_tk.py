"""
Tkinter based window to view display items and (where supported) set targets.
This is the non-notebook counterpart of eco.widgets.display_widget: same
layout and behavior (up/down tweak buttons relative to the real current
value, absolute-value entry committed on Enter/focus-out, no separate "Set"
button), shown in a plain Tk window instead of an ipywidgets box.

Usage (blocking, e.g. plain python script):
    from eco.widgets.display_tk import make_assembly_tk_window
    gui = make_assembly_tk_window(my_assembly, poll_interval=1.0, auto_start=False)
    gui.run()   # blocks until the window is closed

Usage (non-blocking, e.g. terminal IPython session):
    gui = make_assembly_tk_window(my_assembly, poll_interval=1.0)  # auto_start=True by default
    ...
    gui.stop()   # closes the window and stops polling

Note: Tkinter is not thread-safe, so (unlike a first, now-removed attempt at
this) the window is never driven from a background thread. Inside IPython,
`start()` uses IPython's own Tk event-loop integration (the same mechanism
`%matplotlib tk` relies on) to pump the window between prompts on the main
thread; outside of IPython it falls back to a blocking mainloop.
"""
import tkinter as tk
from tkinter import ttk
from typing import Any, List

# Try to import types for isinstance checks if available.
try:
    from eco import Adjustable, Detector
except Exception:
    Adjustable = object
    Detector = object


def _label_of(item: Any, assembly=None) -> str:
    try:
        if hasattr(item, "alias") and hasattr(item.alias, "get_full_name"):
            return (
                item.alias.get_full_name(base=assembly)
                if assembly is not None
                else item.alias.get_full_name()
            )
    except Exception:
        pass
    try:
        if hasattr(item, "name"):
            return str(item.name)
    except Exception:
        pass
    return str(item)


def _coerce_like(value: Any, reference: Any):
    """Coerce a string entry value to the type of `reference` (best effort)."""
    if isinstance(reference, bool):
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(reference, int):
        return int(float(value))
    if isinstance(reference, float):
        return float(value)
    return value


def _default_step_for(value: Any):
    if isinstance(value, int) and not isinstance(value, bool):
        return 1
    return 0.1


class DisplayTk:
    def __init__(self, assembly, poll_interval: float = 1.0, auto_start: bool = True):
        self.assembly = assembly
        self.poll_interval = poll_interval
        self.root = None
        self._entries = []  # list of dicts: item, value_var, input_widget/var, reader

        if auto_start:
            self.start()

    def _get_display_items(self):
        try:
            return list(self.assembly.display_collection())
        except Exception:
            try:
                return list(
                    self.assembly.status_collection.get_list(selection="display")
                )
            except Exception:
                return []

    def _flash_error(self, widget):
        if not isinstance(widget, ttk.Entry):
            return
        try:
            style_name = f"EcoError{id(widget)}.TEntry"
            ttk.Style().configure(style_name, fieldbackground="#ffb3b3")
            old_style = widget.cget("style")
            widget.configure(style=style_name)
        except Exception:
            return

        def _reset():
            try:
                widget.configure(style=old_style)
            except Exception:
                pass

        self.root.after(1200, _reset)

    def _build_layout(self):
        root = self.root
        header = ttk.Frame(root)
        header.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Label(header, text="name", width=28, font=("TkDefaultFont", 9, "bold")).pack(
            side="left"
        )
        ttk.Label(
            header, text="current", width=20, font=("TkDefaultFont", 9, "bold")
        ).pack(side="left")
        ttk.Label(
            header, text="control", font=("TkDefaultFont", 9, "bold")
        ).pack(side="left")
        ttk.Separator(root).pack(fill="x", padx=4)

        for item in self._get_display_items():
            name = _label_of(item, assembly=self.assembly)
            try:
                cur = item.get_current_value()
            except Exception:
                cur = "<error>"

            row = ttk.Frame(root)
            row.pack(fill="x", padx=4, pady=2)
            ttk.Label(row, text=name, width=28).pack(side="left")
            value_var = tk.StringVar(value=str(cur))
            ttk.Label(row, textvariable=value_var, width=20).pack(side="left")
            control = ttk.Frame(row)
            control.pack(side="left", fill="x", expand=True)

            entry = {"item": item, "value_var": value_var, "reader": None}

            # Detector (non-Adjustable): read-only
            if isinstance(item, Detector) and not isinstance(item, Adjustable):
                ttk.Label(control, text="read-only (Detector)").pack(side="left")

            # Adjustable: step field + up/down + absolute entry (commits on Enter/focus-out)
            elif isinstance(item, Adjustable):
                is_plain_scalar = not isinstance(cur, (list, dict, bytes, bytearray))

                step_var = tk.StringVar(value=str(_default_step_for(cur)))
                step_entry = ttk.Entry(control, textvariable=step_var, width=8)
                step_entry.pack(side="left", padx=(0, 4))

                if is_plain_scalar:
                    input_var = tk.StringVar(value=str(cur))
                    input_widget = ttk.Entry(control, textvariable=input_var, width=14)
                    input_widget.pack(side="left", padx=(0, 4))
                    entry["reader"] = lambda iv=input_var: iv.get()

                    def _apply_absolute(
                        _evt=None,
                        it=item,
                        vv=value_var,
                        iv=input_var,
                        iw=input_widget,
                        ref=cur,
                    ):
                        try:
                            newval = _coerce_like(iv.get(), ref)
                            r = it.set_target_value(newval)
                            try:
                                if hasattr(r, "wait"):
                                    r.wait(timeout=5)
                            except Exception:
                                pass
                            new_current = it.get_current_value()
                            vv.set(str(new_current))
                            iv.set(str(new_current))
                        except Exception:
                            self._flash_error(iw)

                    input_widget.bind("<Return>", _apply_absolute)
                    input_widget.bind("<FocusOut>", _apply_absolute)
                else:
                    input_widget = ttk.Label(control, text="n/a", width=14)
                    input_widget.pack(side="left", padx=(0, 4))

                # base is always read fresh from the device, and the absolute
                # entry is resynced to the real current value after every move
                def make_tweak_handler(sign, it=item, vv=value_var, sv=step_var,
                                        iw=input_widget,
                                        input_var=(input_var if is_plain_scalar else None)):
                    def _on_click():
                        try:
                            step = _coerce_like(sv.get(), _default_step_for(cur))
                            base = it.get_current_value()
                            newval = base + sign * step
                            r = it.set_target_value(newval)
                            try:
                                if hasattr(r, "wait"):
                                    r.wait(timeout=5)
                            except Exception:
                                pass
                            new_current = it.get_current_value()
                            vv.set(str(new_current))
                            if input_var is not None:
                                input_var.set(str(new_current))
                        except Exception:
                            self._flash_error(iw)

                    return _on_click

                up_btn = ttk.Button(control, text="▲", width=3,
                                     command=make_tweak_handler(1))
                up_btn.pack(side="left")
                down_btn = ttk.Button(control, text="▼", width=3,
                                      command=make_tweak_handler(-1))
                down_btn.pack(side="left")

            # Fallback: has set_target_value but not recognized as Adjustable
            elif hasattr(item, "set_target_value") and callable(
                getattr(item, "set_target_value")
            ):
                input_var = tk.StringVar(value=str(cur))
                input_widget = ttk.Entry(control, textvariable=input_var, width=20)
                input_widget.pack(side="left")
                entry["reader"] = lambda iv=input_var: iv.get()

                def _apply(
                    _evt=None,
                    it=item,
                    vv=value_var,
                    iv=input_var,
                    iw=input_widget,
                    ref=cur,
                ):
                    try:
                        newval = _coerce_like(iv.get(), ref)
                        r = it.set_target_value(newval)
                        try:
                            if hasattr(r, "wait"):
                                r.wait(timeout=5)
                        except Exception:
                            pass
                        new_current = it.get_current_value()
                        vv.set(str(new_current))
                        iv.set(str(new_current))
                    except Exception:
                        self._flash_error(iw)

                input_widget.bind("<Return>", _apply)
                input_widget.bind("<FocusOut>", _apply)

            else:
                ttk.Label(control, text="—").pack(side="left")

            self._entries.append(entry)

        ttk.Separator(root).pack(fill="x", padx=4, pady=(4, 0))
        close_row = ttk.Frame(root)
        close_row.pack(fill="x", padx=4, pady=4)
        ttk.Button(close_row, text="Close", command=self._on_close).pack(side="left")

    def _poll(self):
        if self.root is None:
            return
        for ent in self._entries:
            try:
                val = ent["item"].get_current_value()
                ent["value_var"].set(str(val))
            except Exception:
                pass
        if self.root is not None:
            self.root.after(int(self.poll_interval * 1000), self._poll)

    def _on_close(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        self.root = None

    def _build_window(self):
        self.root = tk.Tk()
        self.root.title(f"Assembly Display - {getattr(self.assembly, 'name', '')}")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._entries = []
        self._build_layout()
        self.root.after(int(self.poll_interval * 1000), self._poll)

    def run(self):
        """Build and run the window with a blocking Tk mainloop. Use this
        when there is no GUI event-loop integration available to pump the
        window for you (e.g. a plain python script)."""
        if self.root is None:
            self._build_window()
        self.root.mainloop()
        self.root = None

    def start(self):
        """
        Show the window without blocking. Inside an IPython terminal
        session this enables IPython's own Tk event-loop integration
        (the same mechanism used for e.g. `%matplotlib tk`), which pumps
        the window between prompts on the main thread. Note: Tkinter is
        not thread-safe, so this deliberately does not spawn a background
        thread to drive the window (that can crash the interpreter).
        Outside of IPython there is no such integration available, so this
        falls back to the blocking `run()`.
        """
        if self.root is not None:
            return
        ip = None
        try:
            from IPython import get_ipython

            ip = get_ipython()
        except Exception:
            ip = None

        if ip is not None:
            try:
                ip.enable_gui("tk")
            except Exception:
                pass
            self._build_window()
        else:
            self.run()

    def stop(self):
        """Close the window and stop polling."""
        if self.root is not None:
            try:
                self.root.destroy()
            except Exception:
                pass
            self.root = None


def make_assembly_tk_window(assembly, poll_interval: float = 1.0, auto_start: bool = True):
    """Convenience factory, mirrors make_assembly_widget's signature."""
    return DisplayTk(assembly, poll_interval=poll_interval, auto_start=auto_start)
