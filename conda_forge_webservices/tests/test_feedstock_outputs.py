import json
import hmac
import os
import uuid
from unittest import mock
from collections import OrderedDict
import urllib.parse
import base64

import pytest

from binstar_client import BinstarError

from conda_forge_webservices.feedstock_outputs import (
    _copy_feedstock_outputs_from_staging_to_prod,
    _get_ac_api_prod,
    _get_dist,
    _is_valid_feedstock_output,
    relabel_feedstock_outputs,
    validate_feedstock_outputs,
    PROD,
)


@pytest.mark.parametrize("remove", [True, False])
@mock.patch("conda_forge_webservices.feedstock_outputs._dist_exists")
@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api_staging")
@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api_prod")
def test_copy_feedstock_outputs_from_staging_to_prod_exists(
    ac_prod, ac_staging, dist_exists, remove
):
    name = "boo"
    version = "0.1"
    dist = f"noarch/{name}-{version}-py_10.conda"
    src_label = "foo"
    dest_label = "bar"

    dist_exists.return_value = True

    outputs = OrderedDict()
    outputs[dist] = "sdasDsa"

    copied = _copy_feedstock_outputs_from_staging_to_prod(
        outputs,
        src_label,
        dest_label,
        delete=remove,
    )

    assert copied == {dist: True}

    ac_prod.assert_called_once()
    ac_staging.assert_called_once()
    dist_exists.assert_called_once()
    dist_exists.assert_any_call(ac_prod.return_value, "conda-forge", dist)
    ac_prod.return_value.copy.assert_not_called()

    if remove:
        ac_staging.return_value.remove_dist.assert_called_once()
        ac_staging.return_value.remove_dist.assert_any_call(
            "cf-staging",
            name,
            version,
            basename=urllib.parse.quote(dist, safe=""),
        )
    else:
        ac_staging.return_value.remove_dist.assert_not_called()


@pytest.mark.parametrize("error", [False, True])
@pytest.mark.parametrize("remove", [True, False])
@mock.patch("conda_forge_webservices.feedstock_outputs._dist_exists")
@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api_staging")
@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api_prod")
def test_copy_feedstock_outputs_from_staging_to_prod_not_exists(
    ac_prod, ac_staging, dist_exists, remove, error
):
    name = "boo"
    version = "0.1"
    dist = f"noarch/{name}-{version}-py_10.conda"
    src_label = "foo"
    dest_label = "bar"

    dist_exists.side_effect = [False, remove]
    if error:
        ac_prod.return_value.copy.side_effect = [BinstarError("error in copy")]

    outputs = OrderedDict()
    outputs[dist] = "sdasDsa"

    copied = _copy_feedstock_outputs_from_staging_to_prod(
        outputs,
        src_label,
        dest_label,
        delete=remove,
    )

    assert copied == {dist: not error}

    ac_prod.assert_called_once()
    ac_staging.assert_called_once()
    dist_exists.assert_called_once()
    dist_exists.assert_any_call(ac_prod.return_value, "conda-forge", dist)
    ac_prod.return_value.copy.assert_any_call(
        "cf-staging",
        name,
        version,
        basename=urllib.parse.quote(dist, safe=""),
        to_owner="conda-forge",
        from_label=src_label,
        to_label=dest_label,
        update=False,
        replace=False,
    )
    ac_prod.return_value.copy.assert_called_once()

    if not error:
        if remove:
            ac_staging.return_value.remove_dist.assert_called_once()
            ac_staging.return_value.remove_dist.assert_any_call(
                "cf-staging",
                name,
                version,
                basename=urllib.parse.quote(dist, safe=""),
            )
        else:
            ac_staging.return_value.remove_dist.assert_not_called()


@pytest.mark.parametrize("same_label", [False])  # add True if change func back
@pytest.mark.parametrize("valid_output", [True, False])
@pytest.mark.parametrize("valid_copy", [True])  # add False if change func back
@pytest.mark.parametrize("valid_staging_hash", [True, False])
@pytest.mark.parametrize("valid_prod_hash", [True])  # add False if change func back
@mock.patch(
    "conda_forge_webservices.feedstock_outputs._copy_feedstock_outputs_from_staging_to_prod"
)
@mock.patch("conda_forge_webservices.feedstock_outputs._is_valid_output_hash")
@mock.patch("conda_forge_webservices.feedstock_outputs._is_valid_feedstock_output")
def test_validate_feedstock_outputs_badoutputhash(
    valid_out,
    valid_hash,
    copy_fo,
    valid_output,
    valid_staging_hash,
    valid_copy,
    valid_prod_hash,
    same_label,
):
    valid_out.return_value = {
        "noarch/a-0.1-py_0.conda": valid_output,
        "noarch/b-0.1-py_0.conda": not valid_output,
    }
    valid_hash.side_effect = [
        {
            "noarch/a-0.1-py_0.conda": valid_staging_hash,
            "noarch/b-0.1-py_0.conda": not valid_staging_hash,
        },
        {
            "noarch/a-0.1-py_0.conda": valid_prod_hash,
            # change to not valid_prod_hash if change func back
            "noarch/b-0.1-py_0.conda": valid_prod_hash,
        },
    ]
    copy_fo.return_value = {
        "noarch/a-0.1-py_0.conda": valid_copy,
        # change to not valid_copy if change func back
        "noarch/b-0.1-py_0.conda": valid_copy,
    }
    staging_label = "cf-staging-do-not-use-h" + uuid.uuid4().hex
    valid, errs = validate_feedstock_outputs(
        "bar-feedstock",
        {
            "noarch/a-0.1-py_0.conda": "daD",
            "noarch/b-0.1-py_0.conda": "safdsa",
        },
        "md5",
        staging_label,
        # "main",
        # staging_label if not same_label else "main",
    )

    assert valid == {
        "noarch/a-0.1-py_0.conda": valid_output
        and valid_staging_hash
        and valid_copy
        and valid_prod_hash
        and (not same_label),
        "noarch/b-0.1-py_0.conda": (not valid_output)
        and (not valid_staging_hash)
        # change to not valid_copy if change func back
        and (valid_copy)
        # change to not valid_prod_hash if change func back
        and (valid_prod_hash)
        and (not same_label),
    }

    if same_label:
        assert errs == ["destination label must be different from staging label"]
    else:
        valid_staging_hash_a_err = (
            "output noarch/a-0.1-py_0.conda does not "
            "have a valid checksum or correct label on cf-staging"
        ) in errs
        valid_staging_hash_b_err = (
            "output noarch/b-0.1-py_0.conda does not "
            "have a valid checksum or correct label on cf-staging"
        ) in errs
        assert valid_staging_hash_a_err is not valid_staging_hash
        assert valid_staging_hash_b_err is valid_staging_hash

        valid_output_a_err = (
            "output noarch/a-0.1-py_0.conda not allowed for conda-forge/bar-feedstock"
        ) in errs
        valid_output_b_err = (
            "output noarch/b-0.1-py_0.conda not allowed for conda-forge/bar-feedstock"
        ) in errs
        if valid_staging_hash:
            assert valid_output_a_err is not valid_output
        if not valid_staging_hash:
            assert valid_output_b_err is valid_output

        # valid_copy_a_err = (
        #     "output noarch/a-0.1-py_0.conda did not copy to "
        #     f"conda-forge under staging label {staging_label}"
        # ) in errs
        # valid_copy_b_err = (
        #     "output noarch/b-0.1-py_0.conda did not copy to "
        #     f"conda-forge under staging label {staging_label}"
        # ) in errs
        # if valid_output and valid_staging_hash:
        #     assert valid_copy_a_err is not valid_copy
        # if (not valid_output) and (not valid_staging_hash):
        #     assert valid_copy_b_err is valid_copy

        # valid_prod_hash_a_err = (
        #     "output noarch/a-0.1-py_0.conda does not "
        #     "have a valid checksum or correct label on conda-forge"
        # ) in errs
        # valid_prod_hash_b_err = (
        #     "output noarch/b-0.1-py_0.conda does not "
        #     "have a valid checksum or correct label on conda-forge"
        # ) in errs
        # if valid_output and valid_staging_hash and valid_copy:
        #     assert valid_prod_hash_a_err is not valid_prod_hash
        # if (not valid_output) and (not valid_staging_hash) and (not valid_copy):
        #     assert valid_prod_hash_b_err is valid_prod_hash


@pytest.mark.skipif(
    "PROD_BINSTAR_TOKEN" not in os.environ, reason="PROD_BINSTAR_TOKEN not set"
)
@pytest.mark.parametrize(
    "dist,hash_value,res",
    [
        (
            "linux-64/python-3.8.2-h9d8adfe_4_cpython.tar.bz2",
            "7382171fb4c13dbedf98e0bd9b60f165",
            True,
        ),
        # bad hash
        (
            "osx-64/python-3.8.2-hdc38147_4_cpython.tar.bz2",
            "7382171fb4c13dbedf98e0bd9b60f165",
            False,
        ),
        # not a package
        (
            "linux-64/python-3.8.2-h9d8adfe_4_cpython.tar",
            "7382171fb4c13dbedf98e0bd9b60f165",
            False,
        ),
        # bad metadata
        (
            "linux-64/python-3.7-h3f687_4_cpython.tar.bz2",
            "2f347da4a40715a5228412e56fb035d8",
            False,
        ),
    ],
)
def test_get_dist(dist, hash_value, res):
    ac_prod = _get_ac_api_prod()
    data = _get_dist(ac_prod, "conda-forge", dist)
    if data is None:
        assert res is False
    else:
        assert hmac.compare_digest(data["md5"], hash_value) is res


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


@pytest.mark.parametrize("error", [True, False])
@pytest.mark.parametrize("remove_src_label", [True, False])
@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api_prod")
def test_relabel_feedstock_outputs(
    ac_prod,
    remove_src_label,
    error,
):
    if error:
        ac_prod.return_value.copy.side_effect = BinstarError("error in add")

    outputs = ["noarch/boo-0.1-py_10.conda"]

    ac_prod.return_value.distribution.return_value = {"labels": ["foo"]}

    relabeled = relabel_feedstock_outputs(
        outputs,
        "foo",
        "bar",
        remove_src_label=remove_src_label,
    )

    assert relabeled == {"noarch/boo-0.1-py_10.conda": not error}

    ac_prod.assert_called_once()

    ac_prod.return_value.copy.assert_called_once()
    ac_prod.return_value.copy.assert_any_call(
        PROD,
        "boo",
        "0.1",
        basename=urllib.parse.quote("noarch/boo-0.1-py_10.conda", safe=""),
        to_owner=PROD,
        from_label="foo",
        to_label="bar",
        update=False,
        replace=True,
    )

    if remove_src_label and not error:
        ac_prod.return_value.remove_channel.assert_called_once()
        ac_prod.return_value.remove_channel.assert_any_call(
            "foo",
            "conda-forge",
            package="boo",
            version="0.1",
            filename=urllib.parse.quote("noarch/boo-0.1-py_10.conda", safe=""),
        )
    else:
        ac_prod.return_value.remove_channel.assert_not_called()
