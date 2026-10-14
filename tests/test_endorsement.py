"""The "Endorse a Wizard" window froze a seeds run in Northguard (10-13 10:58)."""

import asyncio
from types import SimpleNamespace

from wiz101_auto import ui


class Window:
    def __init__(self, name, visible=True, children=()):
        self._name, self._visible, self._children = name, visible, list(children)

    async def is_visible(self):
        return self._visible

    async def get_windows_with_name(self, name):
        found = []
        for child in self._children:
            if child._name == name:
                found.append(child)
            found.extend(await child.get_windows_with_name(name))
        return found


def client_with(root):
    clicked = []

    async def click_window(window):
        clicked.append(window._name)

    mouse = SimpleNamespace(click_window=click_window)
    return SimpleNamespace(root_window=root, mouse_handler=mouse), clicked


def test_closes_a_visible_endorsement_window(monkeypatch):
    async def no_sleep(_s):
        return None

    monkeypatch.setattr(ui.asyncio, "sleep", no_sleep)
    popup = Window(ui.ENDORSEMENT, children=[Window("MainLayout"), Window(ui.ENDORSE_CLOSE)])
    client, clicked = client_with(Window("root", children=[Window("WorldView", children=[popup])]))
    assert asyncio.run(ui.close_endorsement(client))
    assert clicked == [ui.ENDORSE_CLOSE]


def test_leaves_a_hidden_one_and_survives_a_bad_read():
    hidden = Window(ui.ENDORSEMENT, visible=False, children=[Window(ui.ENDORSE_CLOSE)])
    client, clicked = client_with(Window("root", children=[hidden]))
    assert not asyncio.run(ui.close_endorsement(client))
    assert clicked == []
    assert not asyncio.run(ui.close_endorsement(SimpleNamespace(root_window=None)))
