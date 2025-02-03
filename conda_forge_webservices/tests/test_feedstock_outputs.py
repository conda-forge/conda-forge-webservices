import json
from unittest import mock
from collections import OrderedDict
import urllib.parse
import base64

import pytest

from binstar_client import BinstarError

from conda_forge_webservices.feedstock_outputs import (
    _is_valid_feedstock_output,
    _is_valid_output_hash,
    copy_feedstock_outputs,
    validate_feedstock_outputs,
)


@pytest.mark.parametrize("remove", [True, False])
@mock.patch("conda_forge_webservices.feedstock_outputs._dist_exists")
@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api_staging")
@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api_prod")
def test_copy_feedstock_outputs_exists(ac_prod, ac_staging, dist_exists, remove):
    dist_exists.side_effect = [True, remove]

    outputs = OrderedDict()
    outputs["noarch/boo-0.1-py_10.conda"] = "sdasDsa"

    copied = copy_feedstock_outputs(outputs, "blah")

    assert copied == {"noarch/boo-0.1-py_10.conda": True}

    ac_prod.assert_called_once()
    ac_staging.assert_called_once()

    dist_exists.assert_any_call(
        ac_prod.return_value, "conda-forge", "noarch/boo-0.1-py_10.conda"
    )

    dist_exists.assert_any_call(
        ac_staging.return_value, "cf-staging", "noarch/boo-0.1-py_10.conda"
    )

    if remove:
        ac_staging.return_value.remove_dist.assert_called_once()
        ac_staging.return_value.remove_dist.assert_any_call(
            "cf-staging",
            "boo",
            "0.1",
            basename=urllib.parse.quote("noarch/boo-0.1-py_10.conda", safe=""),
        )


@pytest.mark.parametrize("error", [False, True])
@mock.patch("conda_forge_webservices.feedstock_outputs._dist_exists")
@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api_staging")
@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api_prod")
def test_copy_feedstock_outputs_does_no_exist(ac_prod, ac_staging, dist_exists, error):
    dist_exists.side_effect = [False, True]
    if error:
        ac_prod.return_value.copy.side_effect = [BinstarError("error in copy")]

    outputs = OrderedDict()
    outputs["noarch/boo-0.1-py_10.conda"] = "skldjhasl"

    copied = copy_feedstock_outputs(outputs, "blah")

    assert copied == {"noarch/boo-0.1-py_10.conda": not error}

    ac_prod.assert_called_once()
    ac_staging.assert_called_once()

    print(ac_prod.return_value.copy.call_args_list)
    print(dist_exists.call_args_list)

    dist_exists.assert_any_call(
        ac_prod.return_value,
        "conda-forge",
        "noarch/boo-0.1-py_10.conda",
    )

    ac_prod.return_value.copy.assert_called_once()
    ac_prod.return_value.copy.assert_any_call(
        "cf-staging",
        "boo",
        "0.1",
        basename=urllib.parse.quote("noarch/boo-0.1-py_10.conda", safe=""),
        to_owner="conda-forge",
        from_label="blah",
        to_label="blah",
        update=True,
    )

    if not error:
        dist_exists.assert_any_call(
            ac_staging.return_value,
            "cf-staging",
            "noarch/boo-0.1-py_10.conda",
        )

        ac_staging.return_value.remove_dist.assert_called_once()
        ac_staging.return_value.remove_dist.assert_any_call(
            "cf-staging",
            "boo",
            "0.1",
            basename=urllib.parse.quote("noarch/boo-0.1-py_10.conda", safe=""),
        )


@mock.patch("conda_forge_webservices.feedstock_outputs._is_valid_output_hash")
@mock.patch("conda_forge_webservices.feedstock_outputs._is_valid_feedstock_output")
def test_validate_feedstock_outputs_badoutputhash(valid_out, valid_hash):
    valid_out.return_value = {
        "noarch/a-0.1-py_0.conda": True,
        "noarch/b-0.1-py_0.conda": False,
        "noarch/c-0.1-py_0.conda": True,
        "noarch/d-0.1-py_0.conda": False,
    }
    valid_hash.return_value = {
        "noarch/a-0.1-py_0.conda": False,
        "noarch/b-0.1-py_0.conda": True,
        "noarch/c-0.1-py_0.conda": True,
        "noarch/d-0.1-py_0.conda": False,
    }
    valid, errs = validate_feedstock_outputs(
        "bar-feedstock",
        {
            "noarch/a-0.1-py_0.conda": "daD",
            "noarch/b-0.1-py_0.conda": "safdsa",
            "noarch/c-0.1-py_0.conda": "sadSA",
            "noarch/d-0.1-py_0.conda": "SAdsa",
        },
        "md5",
    )

    assert valid == {
        "noarch/a-0.1-py_0.conda": False,
        "noarch/b-0.1-py_0.conda": False,
        "noarch/c-0.1-py_0.conda": True,
        "noarch/d-0.1-py_0.conda": False,
    }
    assert len(errs) == 4
    assert (
        "output noarch/b-0.1-py_0.conda not allowed for conda-forge/bar-feedstock"
    ) in errs
    assert (
        "output noarch/d-0.1-py_0.conda not allowed for conda-forge/bar-feedstock"
    ) in errs
    assert "output noarch/a-0.1-py_0.conda does not have a valid md5 checksum" in errs
    assert "output noarch/d-0.1-py_0.conda does not have a valid md5 checksum" in errs


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

    valid = _is_valid_output_hash(outputs, "md5")
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
    "project", ["foo-feedstock", "blah", "foo", "blarg-feedstock", "boo-feedstock"]
)
@mock.patch("conda_forge_webservices.feedstock_outputs.requests")
@mock.patch("conda_forge_webservices.feedstock_outputs.package_to_feedstock")
@mock.patch(
    "conda_forge_webservices.feedstock_outputs.get_app_token_for_webservices_only"
)
@mock.patch("conda_forge_webservices.feedstock_outputs._add_feedstock_output")
def test_is_valid_feedstock_output(
    afs_mock,
    gat_mock,
    p2f_mock,
    req_mock,
    monkeypatch,
    project,
    register,
):
    monkeypatch.setenv("GH_TOKEN", "abc123")

    def _get_function(name, *args, **kwargs):
        data = None
        text = None
        if "bar.json" in name:
            assert "b/a/r/bar.json" in name
            data = {"feedstocks": ["foo", "blah"]}
            status = 200
        elif "goo.json" in name:
            assert "g/o/o/goo.json" in name
            data = {"feedstocks": ["blarg"]}
            status = 200
        elif "feedstock_outputs_autoreg_allowlist.yml" in name:
            status = 200
            text = "{}"
        else:
            status = 404

        resp = mock.MagicMock()
        resp.status_code = status
        if data is not None:
            resp.json.return_value = {
                "encoding": "base64",
                "content": base64.standard_b64encode(
                    json.dumps(data).encode("utf-8")
                ).decode("ascii"),
            }
        if text is not None:
            resp.text = text
        return resp

    req_mock.get = _get_function

    def _get_p2f_fun(name):
        if "bar" in name:
            return_value = ["foo", "blah"]
        elif "goo" in name:
            return_value = ["blarg"]
        else:
            return_value = []

        return return_value

    p2f_mock.side_effect = _get_p2f_fun

    outputs = [
        "noarch/bar-0.1-py_0.conda",
        "noarch/goo-0.3-py_10.conda",
        "noarch/glob-0.2-py_12.conda",
    ]

    valid = _is_valid_feedstock_output(
        project,
        outputs,
        register=register,
    )

    if project in ["foo", "foo-feedstock"]:
        assert valid == {
            "noarch/bar-0.1-py_0.conda": True,
            "noarch/goo-0.3-py_10.conda": False,
            "noarch/glob-0.2-py_12.conda": register,
        }
    elif project == "blah":
        assert valid == {
            "noarch/bar-0.1-py_0.conda": True,
            "noarch/goo-0.3-py_10.conda": False,
            "noarch/glob-0.2-py_12.conda": register,
        }
    elif project == "blarg-feedstock":
        assert valid == {
            "noarch/bar-0.1-py_0.conda": False,
            "noarch/goo-0.3-py_10.conda": True,
            "noarch/glob-0.2-py_12.conda": register,
        }
    elif project == "boo-feedstock":
        assert valid == {
            "noarch/bar-0.1-py_0.conda": False,
            "noarch/goo-0.3-py_10.conda": False,
            "noarch/glob-0.2-py_12.conda": register,
        }

    if register:
        assert afs_mock.called_once_with(project.replace("-feedstock", ""), "glob")
    else:
        afs_mock.assert_not_called()
