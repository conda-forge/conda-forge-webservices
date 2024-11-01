import pytest

from ..automerge import _automerge_me


@pytest.mark.parametrize(
    "cfg_bool",
    [
        ({}, False),
        ({"bot": {}}, False),
        ({"bot": {"automerge": False}}, False),
        ({"bot": {"automerge": True}}, True),
    ],
)
def test_automerge_me(cfg_bool):
    if cfg_bool[1]:
        assert _automerge_me(cfg_bool[0])
    else:
        assert not _automerge_me(cfg_bool[0])
