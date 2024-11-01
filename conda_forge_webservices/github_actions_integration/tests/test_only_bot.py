from ..automerge import ALLOWED_USERS


def test_only_bot():
    assert ALLOWED_USERS == ["regro-cf-autotick-bot"]
