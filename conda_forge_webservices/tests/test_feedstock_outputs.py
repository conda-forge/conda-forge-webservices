import os
import json
from unittest import mock
from collections import OrderedDict
import urllib.parse

import pytest

from binstar_client import BinstarError

from conda_forge_webservices.feedstock_outputs import (
    is_valid_feedstock_output,
    _is_valid_output_hash,
    copy_feedstock_outputs,
    validate_feedstock_outputs,
)


@pytest.mark.parametrize('remove', [True, False])
@mock.patch("conda_forge_webservices.feedstock_outputs._dist_exists")
@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api_staging")
@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api_prod")
def test_copy_feedstock_outputs_exists(
    ac_prod, ac_staging, dist_exists, remove
):
    dist_exists.side_effect = [True, remove]

    outputs = OrderedDict()
    outputs["noarch/boo-0.1-py_10.tar.bz2"] = "sdasDsa"

    copied = copy_feedstock_outputs(outputs, "blah")

    assert copied == {"noarch/boo-0.1-py_10.tar.bz2": True}

    ac_prod.assert_called_once()
    ac_staging.assert_called_once()

    dist_exists.assert_any_call(
        ac_prod.return_value,
        "conda-forge",
        "noarch/boo-0.1-py_10.tar.bz2"
    )

    dist_exists.assert_any_call(
        ac_staging.return_value,
        "cf-staging",
        "noarch/boo-0.1-py_10.tar.bz2"
    )

    if remove:
        ac_staging.return_value.remove_dist.assert_called_once()
        ac_staging.return_value.remove_dist.assert_any_call(
            "cf-staging",
            "boo",
            "0.1",
            basename=urllib.parse.quote("noarch/boo-0.1-py_10.tar.bz2", safe="")
        )


@pytest.mark.parametrize('error', [False, True])
@mock.patch("conda_forge_webservices.feedstock_outputs._dist_exists")
@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api_staging")
@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api_prod")
def test_copy_feedstock_outputs_does_no_exist(
    ac_prod, ac_staging, dist_exists, error
):
    dist_exists.side_effect = [False, True]
    if error:
        ac_prod.return_value.copy.side_effect = [BinstarError("error in copy")]

    outputs = OrderedDict()
    outputs["noarch/boo-0.1-py_10.tar.bz2"] = "skldjhasl"

    copied = copy_feedstock_outputs(outputs, "blah")

    assert copied == {"noarch/boo-0.1-py_10.tar.bz2": not error}

    ac_prod.assert_called_once()
    ac_staging.assert_called_once()

    print(ac_prod.return_value.copy.call_args_list)
    print(dist_exists.call_args_list)

    dist_exists.assert_any_call(
        ac_prod.return_value,
        "conda-forge",
        "noarch/boo-0.1-py_10.tar.bz2",
    )

    ac_prod.return_value.copy.assert_called_once()
    ac_prod.return_value.copy.assert_any_call(
        "cf-staging",
        "boo",
        "0.1",
        basename=urllib.parse.quote("noarch/boo-0.1-py_10.tar.bz2", safe=""),
        to_owner="conda-forge",
        from_label="blah",
        to_label="blah",
    )

    if not error:
        dist_exists.assert_any_call(
            ac_staging.return_value,
            "cf-staging",
            "noarch/boo-0.1-py_10.tar.bz2",
        )

        ac_staging.return_value.remove_dist.assert_called_once()
        ac_staging.return_value.remove_dist.assert_any_call(
            "cf-staging",
            "boo",
            "0.1",
            basename=urllib.parse.quote("noarch/boo-0.1-py_10.tar.bz2", safe=""),
        )


@mock.patch("conda_forge_webservices.feedstock_outputs._is_valid_output_hash")
@mock.patch("conda_forge_webservices.feedstock_outputs.is_valid_feedstock_output")
@mock.patch("conda_forge_webservices.feedstock_outputs.is_valid_feedstock_token")
def test_validate_feedstock_outputs_badtoken(
    valid_token, valid_out, valid_hash
):
    valid_token.return_value = False
    valid, errs = validate_feedstock_outputs(
        "bar-feedstock",
        {
            "noarch/a-0.1-py_0.tar.bz2": "sadas",
            "noarch/b-0.2-py_0.tar.bz2": "sadKJHSAL",
        },
        "abc",
    )

    assert not any(v for v in valid.values())
    assert ["invalid feedstock token"] == errs

    valid_out.assert_not_called()
    valid_hash.assert_not_called()


@mock.patch("conda_forge_webservices.feedstock_outputs._is_valid_output_hash")
@mock.patch("conda_forge_webservices.feedstock_outputs.is_valid_feedstock_output")
@mock.patch("conda_forge_webservices.feedstock_outputs.is_valid_feedstock_token")
def test_validate_feedstock_outputs_badoutputhash(
    valid_token, valid_out, valid_hash
):
    valid_token.return_value = True
    valid_out.return_value = {
        "noarch/a-0.1-py_0.tar.bz2": True,
        "noarch/b-0.1-py_0.tar.bz2": False,
        "noarch/c-0.1-py_0.tar.bz2": True,
        "noarch/d-0.1-py_0.tar.bz2": False,
    }
    valid_hash.return_value = {
        "noarch/a-0.1-py_0.tar.bz2": False,
        "noarch/b-0.1-py_0.tar.bz2": True,
        "noarch/c-0.1-py_0.tar.bz2": True,
        "noarch/d-0.1-py_0.tar.bz2": False,
    }
    valid, errs = validate_feedstock_outputs(
        "bar-feedstock",
        {
            "noarch/a-0.1-py_0.tar.bz2": "daD",
            "noarch/b-0.1-py_0.tar.bz2": "safdsa",
            "noarch/c-0.1-py_0.tar.bz2": "sadSA",
            "noarch/d-0.1-py_0.tar.bz2": "SAdsa",
        },
        "abc",
    )

    assert valid == {
        "noarch/a-0.1-py_0.tar.bz2": False,
        "noarch/b-0.1-py_0.tar.bz2": False,
        "noarch/c-0.1-py_0.tar.bz2": True,
        "noarch/d-0.1-py_0.tar.bz2": False,
    }
    assert len(errs) == 4
    assert (
        "output noarch/b-0.1-py_0.tar.bz2 not allowed for "
        "conda-forge/bar-feedstock") in errs
    assert (
        "output noarch/d-0.1-py_0.tar.bz2 not allowed for "
        "conda-forge/bar-feedstock") in errs
    assert "output noarch/a-0.1-py_0.tar.bz2 does not have a valid md5 checksum" in errs
    assert "output noarch/d-0.1-py_0.tar.bz2 does not have a valid md5 checksum" in errs


@mock.patch("conda_forge_webservices.feedstock_outputs.STAGING", new="conda-forge")
def test_is_valid_output_hash():
    outputs = {
        "linux-64/python-3.8.2-h9d8adfe_4_cpython.tar.bz2": (
            "7382171fb4c13dbedf98e0bd9b60f165"
        ),
        # bad hash
        "osx-64/python-3.8.2-hdc38147_4_cpython.tar.bz2": (
            "7382171fb4c13dbedf98e0bd9b60f165"
        ),
        # not a package
        "linux-64/python-3.8.2-h9d8adfe_4_cpython.tar": (
            "7382171fb4c13dbedf98e0bd9b60f165"
        ),
        # bad metadata
        "linux-64/python-3.7-h3f687_4_cpython.tar.bz2": (
            "2f347da4a40715a5228412e56fb035d8"
        ),
    }

    valid = _is_valid_output_hash(outputs)
    assert valid == {
        "linux-64/python-3.8.2-h9d8adfe_4_cpython.tar.bz2": True,
        # bad hash
        "osx-64/python-3.8.2-hdc38147_4_cpython.tar.bz2": False,
        # not a package
        "linux-64/python-3.8.2-h9d8adfe_4_cpython.tar": False,
        # bad metadata
        "linux-64/python-3.7-h3f687_4_cpython.tar.bz2": False,
    }


@pytest.mark.parametrize("register", [True, False])
@pytest.mark.parametrize(
    "project", ["foo", "foo-feedstock", "blah", "blarg", "boo"]
)
@mock.patch("conda_forge_webservices.feedstock_outputs.tmp_directory")
@mock.patch("conda_forge_webservices.feedstock_outputs._run_git_command")
def test_is_valid_feedstock_output(
    git_mock, tmp_mock, tmpdir, monkeypatch, project, register
):
    tmp_mock.return_value.__enter__.return_value = str(
        tmpdir
    )
    monkeypatch.setenv("GH_TOKEN", "abc123")
    os.makedirs(os.path.join(tmpdir, "feedstock-outputs", "outputs"), exist_ok=True)
    with open(
        os.path.join(tmpdir, "feedstock-outputs", "outputs", "bar.json"),
        "w"
    ) as fp:
        json.dump({"feedstocks": ["foo", "blah"]}, fp)

    with open(
        os.path.join(tmpdir, "feedstock-outputs", "outputs", "goo.json"),
        "w"
    ) as fp:
        json.dump({"feedstocks": ["blarg"]}, fp)

    user = "conda-forge"

    outputs = [
        "noarch/bar-0.1-py_0.tar.bz2",
        "noarch/goo-0.3-py_10.tar.bz2",
        "noarch/glob-0.2-py_12.tar.bz2",
    ]

    valid = is_valid_feedstock_output(
        project, outputs, register=register
    )

    git_mock.assert_any_call(
        "clone",
        "--depth=1",
        "https://${GH_TOKEN}@github.com/conda-forge/feedstock-outputs.git"
    )

    if project in ["foo", "foo-feedstock"]:
        assert valid == {
            "noarch/bar-0.1-py_0.tar.bz2": True,
            "noarch/goo-0.3-py_10.tar.bz2": False,
            "noarch/glob-0.2-py_12.tar.bz2": True
        }
    elif project == "blah":
        assert valid == {
            "noarch/bar-0.1-py_0.tar.bz2": True,
            "noarch/goo-0.3-py_10.tar.bz2": False,
            "noarch/glob-0.2-py_12.tar.bz2": True,
        }
    elif project == "blarg":
        assert valid == {
            "noarch/bar-0.1-py_0.tar.bz2": False,
            "noarch/goo-0.3-py_10.tar.bz2": True,
            "noarch/glob-0.2-py_12.tar.bz2": True,
        }
    elif project == "boo":
        assert valid == {
            "noarch/bar-0.1-py_0.tar.bz2": False,
            "noarch/goo-0.3-py_10.tar.bz2": False,
            "noarch/glob-0.2-py_12.tar.bz2": True,
        }

    if register:
        assert os.path.exists(
            os.path.join(tmpdir, "feedstock-outputs", "outputs", "glob.json"))
        with open(
            os.path.join(tmpdir, "feedstock-outputs", "outputs", "glob.json"),
            "r"
        ) as fp:
            data = json.load(fp)
        assert data == {"feedstocks": [project.replace("-feedstock", "")]}

        git_mock.assert_any_call("add", "outputs/glob.json")
        git_mock.assert_any_call(
            "commit",
            "-m",
            "'added output %s for %s/%s'"
            % ("glob", user, project.replace("-feedstock", ""))
        )

        git_mock.assert_any_call("pull", "--commit", "--rebase")
        git_mock.assert_any_call("push")
    else:
        assert len(git_mock.call_args_list) == 2
        assert ("add", "outputs/glob.json") not in git_mock.call_args_list
        assert (
            "commit",
            "-m",
            "added output %s for %s/%s"
            % ("glob", user, project.replace("-feedstock", ""))
        ) not in git_mock.call_args_list
        assert ("pull", "--commit", "--rebase") not in git_mock.call_args_list
        assert ("push",) not in git_mock.call_args_list
        assert not os.path.exists(
            os.path.join(tmpdir, "feedstock-outputs", "outputs", "glob.json"))
