import os

from webservices_dispatch_action.env_management import SensitiveEnv


def test_simple_sensitive_env(env_setup):
    os.environ["GH_TOKEN"] = "hi"
    s = SensitiveEnv()

    s.hide_env_vars()
    assert "GH_TOKEN" not in os.environ

    s.reveal_env_vars()
    assert "GH_TOKEN" in os.environ
    assert os.environ["GH_TOKEN"] == "hi"


def test_ctx_sensitive_env(env_setup):
    os.environ["GH_TOKEN"] = "hi"
    s = SensitiveEnv()

    with s.sensitive_env():
        assert "GH_TOKEN" in os.environ
        assert os.environ["GH_TOKEN"] == "hi"
    assert "GH_TOKEN" not in os.environ


def test_double_sensitive_env(env_setup):
    os.environ["GH_TOKEN"] = "hi"
    os.environ["pwd"] = "hello"
    s = SensitiveEnv()
    s.hide_env_vars()
    s.SENSITIVE_KEYS.append("pwd")
    s.hide_env_vars()
    s.reveal_env_vars()
    assert os.environ["pwd"] == "hello"
    assert os.environ["GH_TOKEN"] == "hi"
