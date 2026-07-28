from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ui.customer_package import CustomerPackageDialog  # noqa: E402


def test_folder_tree_propagates_selection_and_partial_parent_state():
    dialog = object.__new__(CustomerPackageDialog)
    root = os.path.normpath(r"C:\project\!LATEST\02_ПД")
    first = os.path.join(root, "01_ПЗ")
    second = os.path.join(root, "02_СПОЗУ")
    dialog._states = {root: True, first: True, second: True}
    dialog._children = {root: [first, second], first: [], second: []}
    dialog._refresh_item = lambda _path: None
    dialog._summary = lambda: None

    dialog._set_branch(first, False)
    assert dialog._states[root] is None

    dialog._set_branch(second, False)
    assert dialog._states[root] is False

    dialog._set_branch(root, True)
    assert all(state is True for state in dialog._states.values())
