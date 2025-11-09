"""
IPython widget to select one item from a Namespace.required_names and
to choose one item to be selected in 12 hours (separate radio group).

Usage:
    from eco.widgets.namespace_selector import make_namespace_selector
    w = make_namespace_selector(ns)
    display(w)            # w is a widgets.VBox subclass
    cur = w.get_current()
    sched = w.get_scheduled_12h()
    w.on_change(lambda cur, sched: print("changed", cur, sched))
"""
# ...existing code...
from typing import Any, Callable, Iterable, List, Optional, Tuple

import ipywidgets as widgets


def _label_of(item: Any) -> str:
    # prefer alias/full name, then name, then str()
    try:
        if hasattr(item, "alias") and hasattr(item.alias, "get_full_name"):
            return item.alias.get_full_name()
    except Exception:
        pass
    try:
        if hasattr(item, "name"):
            return str(item.name)
    except Exception:
        pass
    return str(item)


class NamespaceSelector(widgets.VBox):
    def __init__(
        self,
        namespace: Any,
        required_names_attr: str = "required_names",
        initial: Optional[Any] = None,
        scheduled_initial: Optional[Any] = None,
        none_label: str = "None",
    ):
        """
        namespace: object containing an iterable attribute required_names_attr
                   (list of items or names). Each entry can be any object.
        initial: optional item from the required_names to pre-select (value compared by identity).
        scheduled_initial: optional item to pre-select for 12-hour selection (or None).
        """
        # collect items
        items = getattr(namespace, required_names_attr, None)
        if items is None:
            items = []
        # normalize to list
        items = list(items)

        # build options as (label, value) pairs
        self._options: List[Tuple[str, Any]] = [( _label_of(it), it ) for it in items]

        # radio for "current selection"
        rb_options = [(lbl, val) for lbl, val in self._options]
        self.current_rb = widgets.RadioButtons(
            options=rb_options,
            value=(initial if initial is not None else (rb_options[0][1] if rb_options else None)),
            description="Select",
            layout=widgets.Layout(width="100%"),
        )

        # radio for "selected in 12 hours" — include explicit None option
        rb12_options = [(none_label, None)] + [(lbl, val) for lbl, val in self._options]
        self.scheduled_rb = widgets.RadioButtons(
            options=rb12_options,
            value=(scheduled_initial if scheduled_initial is not None else None),
            description="In 12h",
            layout=widgets.Layout(width="100%"),
        )

        super().__init__([widgets.HTML(value="<b>Required items</b>"), self.current_rb,
                          widgets.HTML(value="<b>Mark component to select in 12 hours</b>"), self.scheduled_rb])

        # callbacks list called with (current_value, scheduled_value)
        self._cbs: List[Callable[[Any, Any], None]] = []

        # attach observers
        self.current_rb.observe(self._on_change, names="value")
        self.scheduled_rb.observe(self._on_change, names="value")

    def _on_change(self, change):
        cur = self.get_current()
        sched = self.get_scheduled_12h()
        for cb in list(self._cbs):
            try:
                cb(cur, sched)
            except Exception:
                # swallow callback errors to keep widget responsive
                pass

    def get_current(self) -> Optional[Any]:
        """Return the currently selected item (or None)."""
        return self.current_rb.value

    def get_scheduled_12h(self) -> Optional[Any]:
        """Return the item selected for 12 hours from now (or None)."""
        return self.scheduled_rb.value

    def set_current(self, item: Any) -> None:
        """Programmatically set current selection (item must be one of options or None)."""
        # allow None if present in options (rare); otherwise ignore
        values = [v for (_, v) in self.current_rb.options]
        if item in values:
            self.current_rb.value = item

    def set_scheduled_12h(self, item: Optional[Any]) -> None:
        """Programmatically set 12h selection (item must be one of options or None)."""
        values = [v for (_, v) in self.scheduled_rb.options]
        if item in values:
            self.scheduled_rb.value = item

    def on_change(self, cb: Callable[[Any, Any], None]) -> None:
        """Register a callback called as cb(current, scheduled) on changes."""
        if callable(cb):
            self._cbs.append(cb)


def make_namespace_selector(
    namespace: Any,
    required_names_attr: str = "required_names",
    initial: Optional[Any] = None,
    scheduled_initial: Optional[Any] = None,
) -> NamespaceSelector:
    """Convenience factory."""
    return NamespaceSelector(
        namespace,
        required_names_attr=required_names_attr,
        initial=initial,
        scheduled_initial=scheduled_initial,
    )
# ...existing code...
```# filepath: /home/lemke_h/mypy/eco/eco/widgets/namespace_selector.py
"""
IPython widget to select one item from a Namespace.required_names and
to choose one item to be selected in 12 hours (separate radio group).

Usage:
    from eco.widgets.namespace_selector import make_namespace_selector
    w = make_namespace_selector(ns)
    display(w)            # w is a widgets.VBox subclass
    cur = w.get_current()
    sched = w.get_scheduled_12h()
    w.on_change(lambda cur, sched: print("changed", cur, sched))
"""
# ...existing code...
from typing import Any, Callable, Iterable, List, Optional, Tuple

import ipywidgets as widgets


def _label_of(item: Any) -> str:
    # prefer alias/full name, then name, then str()
    try:
        if hasattr(item, "alias") and hasattr(item.alias, "get_full_name"):
            return item.alias.get_full_name()
    except Exception:
        pass
    try:
        if hasattr(item, "name"):
            return str(item.name)
    except Exception:
        pass
    return str(item)


class NamespaceSelector(widgets.VBox):
    def __init__(
        self,
        namespace: Any,
        required_names_attr: str = "required_names",
        initial: Optional[Any] = None,
        scheduled_initial: Optional[Any] = None,
        none_label: str = "None",
    ):
        """
        namespace: object containing an iterable attribute required_names_attr
                   (list of items or names). Each entry can be any object.
        initial: optional item from the required_names to pre-select (value compared by identity).
        scheduled_initial: optional item to pre-select for 12-hour selection (or None).
        """
        # collect items
        items = getattr(namespace, required_names_attr, None)
        if items is None:
            items = []
        # normalize to list
        items = list(items)

        # build options as (label, value) pairs
        self._options: List[Tuple[str, Any]] = [( _label_of(it), it ) for it in items]

        # radio for "current selection"
        rb_options = [(lbl, val) for lbl, val in self._options]
        self.current_rb = widgets.RadioButtons(
            options=rb_options,
            value=(initial if initial is not None else (rb_options[0][1] if rb_options else None)),
            description="Select",
            layout=widgets.Layout(width="100%"),
        )

        # radio for "selected in 12 hours" — include explicit None option
        rb12_options = [(none_label, None)] + [(lbl, val) for lbl, val in self._options]
        self.scheduled_rb = widgets.RadioButtons(
            options=rb12_options,
            value=(scheduled_initial if scheduled_initial is not None else None),
            description="In 12h",
            layout=widgets.Layout(width="100%"),
        )

        super().__init__([widgets.HTML(value="<b>Required items</b>"), self.current_rb,
                          widgets.HTML(value="<b>Mark component to select in 12 hours</b>"), self.scheduled_rb])

        # callbacks list called with (current_value, scheduled_value)
        self._cbs: List[Callable[[Any, Any], None]] = []

        # attach observers
        self.current_rb.observe(self._on_change, names="value")
        self.scheduled_rb.observe(self._on_change, names="value")

    def _on_change(self, change):
        cur = self.get_current()
        sched = self.get_scheduled_12h()
        for cb in list(self._cbs):
            try:
                cb(cur, sched)
            except Exception:
                # swallow callback errors to keep widget responsive
                pass

    def get_current(self) -> Optional[Any]:
        """Return the currently selected item (or None)."""
        return self.current_rb.value

    def get_scheduled_12h(self) -> Optional[Any]:
        """Return the item selected for 12 hours from now (or None)."""
        return self.scheduled_rb.value

    def set_current(self, item: Any) -> None:
        """Programmatically set current selection (item must be one of options or None)."""
        # allow None if present in options (rare); otherwise ignore
        values = [v for (_, v) in self.current_rb.options]
        if item in values:
            self.current_rb.value = item

    def set_scheduled_12h(self, item: Optional[Any]) -> None:
        """Programmatically set 12h selection (item must be one of options or None)."""
        values = [v for (_, v) in self.scheduled_rb.options]
        if item in values:
            self.scheduled_rb.value = item

    def on_change(self, cb: Callable[[Any, Any], None]) -> None:
        """Register a callback called as cb(current, scheduled) on changes."""
        if callable(cb):
            self._cbs.append(cb)


def make_namespace_selector(
    namespace: Any,
    required_names_attr: str = "required_names",
    initial: Optional[Any] = None,
    scheduled_initial: Optional[Any] = None,
) -> NamespaceSelector:
    """Convenience factory."""
    return NamespaceSelector(
        namespace,
        required_names_attr=required_names_attr,
        initial=initial,
        scheduled_initial=scheduled_initial,
    )
# ...existing code...