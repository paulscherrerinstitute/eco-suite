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
import enum
import tkinter as tk
from tkinter import ttk
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List

# Try to import types for isinstance checks if available.
try:
    from eco import Adjustable, Detector
    from eco import AdjustableEnum as AdjustableEnumProtocol
    from eco import DetectorEnum as DetectorEnumProtocol
    from eco.elements.assembly import Assembly
except Exception:
    Adjustable = object
    Detector = object

    class AdjustableEnumProtocol:
        pass

    class DetectorEnumProtocol:
        pass

    Assembly = object

_PREFETCH_ERROR = object()  # sentinel: a prefetched read raised


def _format_value(value):
    """Show enum-valued readbacks by their label (describer) rather than the
    underlying integer."""
    if isinstance(value, enum.Enum):
        return value.name
    return str(value)


def _enum_options(item, cur):
    """Return the ordered list of enum label strings for an enum-enabled item
    (anything satisfying the eco.elements.protocols.AdjustableEnum/DetectorEnum
    protocol, e.g. AdjustablePvEnum / eco.elements.adjustable.AdjustableEnum),
    or None if it isn't enum-enabled."""
    if isinstance(item, (AdjustableEnumProtocol, DetectorEnumProtocol)):
        strs = getattr(item, "enum_strs", None)
        if strs:
            try:
                return [str(s) for s in strs]
            except Exception:
                pass
    # fall back to the enum class of the current value (e.g. vacuum Valve,
    # which composes ValveState from two plain booleans rather than exposing
    # enum_strs itself)
    if isinstance(cur, enum.Enum):
        members = sorted(
            type(cur).__members__.items(), key=lambda kv: kv[1].value
        )
        return [name for name, _ in members]
    return None


def _prefetch_values(items, max_workers=8):
    """Read all initial values concurrently so building the window for a large
    assembly is bounded by the slowest single readback rather than their sum.
    Returns {id(item): value_or__PREFETCH_ERROR}. Never raises."""
    values = {}
    items = list(items)
    if not items:
        return values

    def _read(it):
        return it.get_current_value()

    try:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as ex:
            futures = {ex.submit(_read, it): it for it in items}
            for fut, it in futures.items():
                try:
                    values[id(it)] = fut.result()
                except Exception:
                    values[id(it)] = _PREFETCH_ERROR
    except Exception:
        pass
    return values


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


def _is_tweakable(value: Any) -> bool:
    """Only plain numeric values support +/- step tweaking; e.g. strings don't."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class DisplayTk:
    def __init__(
        self,
        assembly,
        poll_interval: float = 1.0,
        auto_start: bool = True,
        show_hidden: bool = False,
    ):
        self.assembly = assembly
        self.poll_interval = poll_interval
        self.show_hidden = show_hidden
        self.root = None
        self._entries = []  # list of dicts: item, value_var, input_widget/var, reader
        self._child_windows = {}  # id(child_assembly) -> DisplayTk shown in a Toplevel
        self._poll_after_id = None
        self._prefetched = {}
        self._memory_browser = None  # lazily-built "memories" sub-window, see _open_memory_browser

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

    def _get_hidden_items(self):
        display_items = self._get_display_items()
        try:
            all_items = list(self.assembly.status_collection.get_list())
        except Exception:
            return []
        return [it for it in all_items if it not in display_items]

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

    def _make_scrollable_area(self, parent):
        """Wrap scrollable content in a Canvas+Scrollbar and return the inner
        frame to pack rows into, so the window doesn't grow unbounded when it
        has many items (see _cap_window_height)."""
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True)
        canvas = tk.Canvas(container, highlightthickness=0)
        vsb = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(_evt=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(evt):
            canvas.itemconfig(inner_id, width=evt.width)

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def _on_mousewheel(evt):
            canvas.yview_scroll(int(-1 * (evt.delta / 120)), "units")

        def _bind_wheel(_evt=None):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
            canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))

        def _unbind_wheel(_evt=None):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)

        return inner

    def _cap_window_height(self):
        """Keep the window from growing past the screen; the scroll area
        (added in _build_layout) takes over once content exceeds this."""
        try:
            self.root.update_idletasks()
            max_h = int(self.root.winfo_screenheight() * 0.8)
            req_h = self.root.winfo_reqheight()
            req_w = self.root.winfo_reqwidth()
            if req_h > max_h:
                self.root.geometry(f"{req_w}x{max_h}")
        except Exception:
            pass

    def _toggle_hidden(self, btn, box):
        if box.winfo_ismapped():
            box.pack_forget()
            btn.config(text="expand hidden")
        else:
            box.pack(fill="x", padx=4, pady=(0, 4))
            btn.config(text="hide hidden")

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

        scroll_area = self._make_scrollable_area(root)

        display_items = self._get_display_items()
        hidden_items = self._get_hidden_items()
        # read every initial value concurrently up front, so opening a large
        # assembly is bounded by the slowest single readback, not their sum
        self._prefetched = _prefetch_values(list(display_items) + list(hidden_items))

        for item in display_items:
            self._build_item_row(scroll_area, item)

        if hidden_items:
            toggle_row = ttk.Frame(scroll_area)
            toggle_row.pack(fill="x", padx=4, pady=(6, 2))
            hidden_box = tk.Frame(scroll_area, bg="#e0e0e0")
            toggle_btn = ttk.Button(
                toggle_row,
                text="hide hidden" if self.show_hidden else "expand hidden",
            )
            toggle_btn.config(
                command=lambda b=toggle_btn, box=hidden_box: self._toggle_hidden(b, box)
            )
            toggle_btn.pack(side="left")

            for item in hidden_items:
                self._build_item_row(hidden_box, item)

            if self.show_hidden:
                hidden_box.pack(fill="x", padx=4, pady=(0, 4))

        ttk.Separator(root).pack(fill="x", padx=4, pady=(4, 0))
        close_row = ttk.Frame(root)
        close_row.pack(fill="x", padx=4, pady=4)
        ttk.Button(close_row, text="Close", command=self._on_close).pack(side="left")
        if hasattr(self.assembly, "memory"):
            ttk.Button(
                close_row, text="memories", command=self._open_memory_browser
            ).pack(side="left", padx=(4, 0))

    def _build_item_row(self, parent, item):
        name = _label_of(item, assembly=self.assembly)
        prefetched = getattr(self, "_prefetched", {})
        if id(item) in prefetched:
            cur = prefetched[id(item)]
            if cur is _PREFETCH_ERROR:
                cur = "<error>"
        else:
            try:
                cur = item.get_current_value()
            except Exception:
                cur = "<error>"

        row = ttk.Frame(parent)
        row.pack(fill="x", padx=4, pady=2)
        if isinstance(item, Assembly):
            name_label = ttk.Label(
                row, text=name, width=28, foreground="#2a6fdb", cursor="hand2"
            )
            name_label.pack(side="left")
            name_label.bind(
                "<Button-1>", lambda _evt, it=item: self._open_child_window(it)
            )
        else:
            ttk.Label(row, text=name, width=28).pack(side="left")
        value_var = tk.StringVar(value=_format_value(cur))
        ttk.Label(row, textvariable=value_var, width=20).pack(side="left")
        control = ttk.Frame(row)
        control.pack(side="left", fill="x", expand=True)

        entry = {"item": item, "value_var": value_var, "reader": None}

        # Detector (non-Adjustable): read-only
        if isinstance(item, Detector) and not isinstance(item, Adjustable):
            ttk.Label(control, text="read-only (Detector)").pack(side="left")

        # Adjustable: step field + up/down + absolute entry (commits on Enter/focus-out)
        elif isinstance(item, Adjustable):
            original_value = cur
            enum_opts = _enum_options(item, cur)
            changer_ref = {"changer": None}
            entry["changer_ref"] = changer_ref

            if enum_opts is not None:
                # ENUM adjustable: dropdown selector, current readback preselected
                cur_label = _format_value(cur)
                combo_var = tk.StringVar(
                    value=cur_label if cur_label in enum_opts
                    else (enum_opts[0] if enum_opts else "")
                )
                combo = ttk.Combobox(
                    control, textvariable=combo_var, values=enum_opts,
                    state="readonly", width=14,
                )
                combo.pack(side="left", padx=(0, 4))
                entry["reader"] = lambda cv=combo_var: cv.get()
                entry["combo_var"] = combo_var
                entry["enum_opts"] = enum_opts

                def _enum_set(label, cvar=combo_var, vv=value_var, it=item,
                              cref=changer_ref):
                    try:
                        r = it.set_target_value(label)
                        cref["changer"] = r
                        try:
                            if hasattr(r, "wait"):
                                r.wait(timeout=5)
                        except Exception:
                            pass
                        try:
                            new_cur = it.get_current_value()
                        except Exception:
                            new_cur = None
                        if new_cur is not None:
                            lbl = _format_value(new_cur)
                            vv.set(lbl)
                            if lbl in enum_opts:
                                cvar.set(lbl)
                    except Exception:
                        pass

                def _on_combo_selected(_evt=None, cvar=combo_var):
                    _enum_set(cvar.get())

                combo.bind("<<ComboboxSelected>>", _on_combo_selected)

                def _on_stop(it=item, cref=changer_ref):
                    changer = cref.get("changer")
                    if changer is not None and hasattr(changer, "stop"):
                        try:
                            changer.stop()
                        except Exception:
                            pass

                def _on_reset(ref=original_value):
                    _enum_set(ref.name if isinstance(ref, enum.Enum) else ref)

                stop_btn = ttk.Button(control, text="\U0001f6d1", width=3,
                                      command=_on_stop)
                stop_btn.pack(side="left", padx=(4, 0))
                reset_btn = ttk.Button(control, text="↺", width=3, command=_on_reset)
                reset_btn.pack(side="left")

            else:
                is_plain_scalar = not isinstance(cur, (list, dict, bytes, bytearray))
                tweakable = _is_tweakable(cur)

                if tweakable:
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
                        cref=changer_ref,
                    ):
                        try:
                            newval = _coerce_like(iv.get(), ref)
                            r = it.set_target_value(newval)
                            cref["changer"] = r
                            try:
                                if hasattr(r, "wait"):
                                    r.wait(timeout=5)
                            except Exception:
                                pass
                            new_current = it.get_current_value()
                            vv.set(_format_value(new_current))
                            iv.set(str(new_current))
                        except Exception:
                            self._flash_error(iw)

                    input_widget.bind("<Return>", _apply_absolute)
                    input_widget.bind("<FocusOut>", _apply_absolute)
                else:
                    input_widget = ttk.Label(control, text="n/a", width=14)
                    input_widget.pack(side="left", padx=(0, 4))

                if tweakable:
                    # base is always read fresh from the device, and the absolute
                    # entry is resynced to the real current value after every move
                    def make_tweak_handler(sign, it=item, vv=value_var, sv=step_var,
                                            iw=input_widget, cref=changer_ref,
                                            input_var=(input_var if is_plain_scalar else None)):
                        def _on_click():
                            try:
                                step = _coerce_like(sv.get(), _default_step_for(cur))
                                base = it.get_current_value()
                                newval = base + sign * step
                                r = it.set_target_value(newval)
                                cref["changer"] = r
                                try:
                                    if hasattr(r, "wait"):
                                        r.wait(timeout=5)
                                except Exception:
                                    pass
                                new_current = it.get_current_value()
                                vv.set(_format_value(new_current))
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

                def _on_stop(it=item, cref=changer_ref):
                    changer = cref.get("changer")
                    if changer is not None and hasattr(changer, "stop"):
                        try:
                            changer.stop()
                        except Exception:
                            pass

                def _on_reset(
                    it=item, vv=value_var,
                    iw=input_widget if is_plain_scalar else None,
                    input_var=(input_var if is_plain_scalar else None),
                    ref=original_value, cref=changer_ref,
                ):
                    try:
                        r = it.set_target_value(ref)
                        cref["changer"] = r
                        try:
                            if hasattr(r, "wait"):
                                r.wait(timeout=5)
                        except Exception:
                            pass
                        new_current = it.get_current_value()
                        vv.set(_format_value(new_current))
                        if input_var is not None:
                            input_var.set(str(new_current))
                    except Exception:
                        if iw is not None:
                            self._flash_error(iw)

                stop_btn = ttk.Button(control, text="🛑", width=3, command=_on_stop)
                stop_btn.pack(side="left", padx=(4, 0))
                reset_btn = ttk.Button(control, text="↺", width=3, command=_on_reset)
                reset_btn.pack(side="left")

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

    def _poll(self):
        if self.root is None:
            return
        for ent in self._entries:
            try:
                val = ent["item"].get_current_value()
                text = _format_value(val)
                ent["value_var"].set(text)
                combo_var = ent.get("combo_var")
                # keep the dropdown selection tracking the readback (e.g. a
                # valve settling from a stale/mismatched selection to its
                # true OPEN/CLOSED state); setting the StringVar directly
                # does not fire <<ComboboxSelected>>, so this can't loop back
                # into _enum_set and issue a spurious write.
                if combo_var is not None and text in ent.get("enum_opts", ()):
                    combo_var.set(text)
            except Exception:
                pass
        if self.root is not None:
            self._poll_after_id = self.root.after(
                int(self.poll_interval * 1000), self._poll
            )

    def _on_close(self):
        self.stop()

    def _open_child_window(self, child_assembly):
        existing = self._child_windows.get(id(child_assembly))
        if existing is not None and existing.root is not None:
            try:
                existing.root.deiconify()
                existing.root.lift()
                existing.root.focus_force()
                return
            except Exception:
                pass
        top = tk.Toplevel(self.root)
        child = DisplayTk(child_assembly, poll_interval=self.poll_interval, auto_start=False)
        child._build_window(top)
        self._child_windows[id(child_assembly)] = child

    def _open_memory_browser(self):
        if self._memory_browser is not None and self._memory_browser.top is not None:
            try:
                self._memory_browser.top.deiconify()
                self._memory_browser.top.lift()
                self._memory_browser.top.focus_force()
                return
            except Exception:
                pass
        from eco.widgets.memory_widget import make_memory_browser_tk

        self._memory_browser = make_memory_browser_tk(self.assembly, parent=self.root)

    def _build_window(self, root=None):
        self.root = root if root is not None else tk.Tk()
        self.root.title(f"Assembly Display - {getattr(self.assembly, 'name', '')}")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._entries = []
        self._poll_after_id = None
        self._build_layout()
        self._poll_after_id = self.root.after(
            int(self.poll_interval * 1000), self._poll
        )
        self._cap_window_height()

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
        """Close the window (and any open child-assembly windows) and stop polling."""
        for child in list(self._child_windows.values()):
            try:
                child.stop()
            except Exception:
                pass
        self._child_windows.clear()
        if self._memory_browser is not None:
            try:
                self._memory_browser.top.destroy()
            except Exception:
                pass
            self._memory_browser = None
        if self.root is not None:
            if getattr(self, "_poll_after_id", None) is not None:
                try:
                    self.root.after_cancel(self._poll_after_id)
                except Exception:
                    pass
                self._poll_after_id = None
            try:
                self.root.destroy()
            except Exception:
                pass
            self.root = None


def make_assembly_tk_window(
    assembly,
    poll_interval: float = 1.0,
    auto_start: bool = True,
    show_hidden: bool = False,
):
    """Convenience factory, mirrors make_assembly_widget's signature."""
    return DisplayTk(
        assembly,
        poll_interval=poll_interval,
        auto_start=auto_start,
        show_hidden=show_hidden,
    )
