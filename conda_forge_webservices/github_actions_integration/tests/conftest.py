import os

import pytest

from conda_forge_webservices.github_actions_integration import global_sensitive_env


@pytest.fixture
def env_setup():
    global_sensitive_env.reveal_env_vars()
    old_pwd = os.environ.pop("GH_TOKEN", None)
    os.environ["GH_TOKEN"] = "unpassword"
    global_sensitive_env.hide_env_vars()

    old_pwd2 = os.environ.pop("pwd", None)
    os.environ["pwd"] = "pwd"

    yield

    global_sensitive_env.reveal_env_vars()
    if old_pwd:
        os.environ["GH_TOKEN"] = old_pwd
    global_sensitive_env.hide_env_vars()

    if old_pwd2:
        os.environ["pwd"] = old_pwd2
