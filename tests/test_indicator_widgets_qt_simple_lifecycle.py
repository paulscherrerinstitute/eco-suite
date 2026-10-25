"""A simple Qt gadget deleted while its poll thread keeps reading must stop
quietly -- a reading rendered into the deleted widget used to raise inside a
Qt slot, which aborts the process under PyQt."""

import time

import pytest

pytest.importorskip("qtpy")

from qtpy import QtCore, QtWidgets

from eco.widgets import indicator_widgets_qt_simple as gadgets


class Item:
    name = "x"
    reads = 0

    def get_current_value(self):
        Item.reads += 1
        return 1.0


def test_a_deleted_gadget_stops_reading_instead_of_aborting():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    box = gadgets.bar_gauge(Item(), poll_interval=0.05)
    for _ in range(10):
        app.processEvents()
        time.sleep(0.02)
    box.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    for _ in range(20):  # readings arriving after the deletion
        app.processEvents()
        time.sleep(0.02)
    reads = Item.reads
    time.sleep(0.3)
    assert Item.reads <= reads + 1  # the poll thread stopped
