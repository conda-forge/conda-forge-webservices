import os
import json
from unittest import mock
from collections import OrderedDict

import pytest

from binstar_client import BinstarError

from conda_forge_webservices.feedstock_outputs import (
    is_valid_feedstock_output,
    _is_valid_output_hash,
    copy_feedstock_outputs,
    validate_feedstock_outputs,
)


@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api")
def test_copy_feedstock_outputs(ac):
    ac.return_value.copy.side_effect = [True, BinstarError("blah")]

    outputs = OrderedDict()
    outputs["boo"] = {"version": "1", "name": "boohoo"}
    outputs["blah"] = {"version": "2", "name": "blahha"}

    copied = copy_feedstock_outputs(outputs, "blah")

    assert copied == {"boo": True, "blah": False}

    assert ac.return_value.copy.called_with(
        "cf-staging",
        "boohoo",
        "1",
        basename="boo",
        to_owner="conda-forge",
        from_label="blahh",
        to_label="blahh",
    )

    assert ac.return_value.remove_dist.called_once_with(
        "cf-staging",
        "boohoo",
        "1",
        basename="boo",
    )

    assert ac.return_value.copy.called_with(
        "cf-staging",
        "blahha",
        "2",
        basename="blah",
        to_owner="conda-forge",
        from_label="blahh",
        to_label="blahh",
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
        {"a": {}, "b": {}},
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
        "a-name": True,
        "b-name": False,
        "c-name": True,
        "d-name": False,
    }
    valid_hash.return_value = {
        "a": False,
        "b": True,
        "c": True,
        "d": False,
    }
    valid, errs = validate_feedstock_outputs(
        "bar-feedstock",
        {
            "a": {"name": "a-name", "version": 10, "md5": 100},
            "b": {"name": "b-name", "version": 10, "md5": 100},
            "c": {"name": "c-name", "version": 10, "md5": 100},
            "d": {"name": "d-name", "version": 10, "md5": 100},
        },
        "abc",
    )

    assert valid == {
        "a": False,
        "b": False,
        "c": True,
        "d": False,
    }
    assert len(errs) == 4
    assert "output b not allowed for conda-forge/bar-feedstock" in errs
    assert "output d not allowed for conda-forge/bar-feedstock" in errs
    assert "output a does not have a valid md5 checksum" in errs
    assert "output d does not have a valid md5 checksum" in errs


@mock.patch("conda_forge_webservices.feedstock_outputs.STAGING", new="conda-forge")
def test_is_valid_output_hash():
    outputs = {
        "linux-64/python-3.8.2-h9d8adfe_4_cpython.tar.bz2": {
            "name": "python",
            "version": "3.8.2",
            "md5": "7382171fb4c13dbedf98e0bd9b60f165",
        },
        # bad hash
        "osx-64/python-3.8.2-hdc38147_4_cpython.tar.bz2": {
            "name": "python",
            "version": "3.8.2",
            "md5": "7382171fb4c13dbedf98e0bd9b60f165",
        },
        # not a package
        "linux-64/python-3.8.2-h9d8adfe_4_cpython.tar": {
            "name": "python",
            "version": "3.8.2",
            "md5": "7382171fb4c13dbedf98e0bd9b60f165",
        },
        # bad metadata
        "linux-64/python-3.7.6-h357f687_4_cpython.tar.bz2": {
            "name": "dskljfals",
            "version": "3.4.5",
            "md5": "2f347da4a40715a5228412e56fb035d8",
        },
    }

    valid = _is_valid_output_hash(outputs)
    assert valid == {
        "linux-64/python-3.8.2-h9d8adfe_4_cpython.tar.bz2": True,
        # bad hash
        "osx-64/python-3.8.2-hdc38147_4_cpython.tar.bz2": False,
        # not a package
        "linux-64/python-3.8.2-h9d8adfe_4_cpython.tar": False,
        # bad metadata
        "linux-64/python-3.7.6-h357f687_4_cpython.tar.bz2": False,
    }


@pytest.mark.parametrize("register", [True, False])
@pytest.mark.parametrize(
    "project", ["foo", "foo-feedstock", "blah", "blarg", "boo"]
)
@mock.patch("conda_forge_webservices.feedstock_outputs.tempfile")
@mock.patch("conda_forge_webservices.feedstock_outputs._run_git_command")
def test_is_valid_feedstock_output(
    git_mock, tmp_mock, tmpdir, monkeypatch, project, register
):
    tmp_mock.TemporaryDirectory.return_value.__enter__.return_value = str(
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

    outputs = ["bar", "goo", "glob"]

    valid = is_valid_feedstock_output(
        project, outputs, register=register
    )

    assert git_mock.called_with(
        "clone",
        "--depth=1",
        "https://${GH_TOKEN}@github.com/conda-forge/feedstock-outputs.git"
    )

    if project in ["foo", "foo-feedstock"]:
        assert valid == {"bar": True, "goo": False, "glob": True}
    elif project == "blah":
        assert valid == {"bar": True, "goo": False, "glob": True}
    elif project == "blarg":
        assert valid == {"bar": False, "goo": True, "glob": True}
    elif project == "boo":
        assert valid == {"bar": False, "goo": False, "glob": True}

    if register:
        assert os.path.exists(
            os.path.join(tmpdir, "feedstock-outputs", "outputs", "glob.json"))
        with open(
            os.path.join(tmpdir, "feedstock-outputs", "outputs", "glob.json"),
            "r"
        ) as fp:
            data = json.load(fp)
        assert data == {"feedstocks": [project.replace("-feedstock", "")]}

        assert git_mock.called_with("add", "outputs/glob.json")
        assert git_mock.called_with(
            "commit",
            "-m",
            "added output %s %s/%s"
            % ("glob", user, project.replace("-feedstock", ""))
        )
        assert git_mock.called_with("pull", "--commit", "--rebase")
        assert git_mock.called_with("push")
    else:
        assert ("add", "outputs/glob.json") not in git_mock.call_args_list
        assert (
            "commit",
            "-m",
            "added output %s %s/%s"
            % ("glob", user, project.replace("-feedstock", ""))
        ) not in git_mock.call_args_list
        assert ("pull", "--commit", "--rebase") not in git_mock.call_args_list
        assert ("push",) not in git_mock.call_args_list
        assert not os.path.exists(
            os.path.join(tmpdir, "feedstock-outputs", "outputs", "glob.json"))
