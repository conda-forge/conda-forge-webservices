"""
This module registers and validates feedstock outputs.
"""
import tempfile
import os
import json
import hmac
import urllib.parse
import subprocess

from binstar_client.utils import get_server_api
from binstar_client import BinstarError
import git

from conda_smithy.feedstock_tokens import is_valid_feedstock_token

from .utils import pushd

STAGING = "cf-staging"
PROD = "conda-forge"
OUTPUTS_REPO = "https://${GH_TOKEN}@github.com/conda-forge/feedstock-outputs.git"
TOKENS_REPO = "https://${GH_TOKEN}@github.com/conda-forge/feedstock-tokens.git"


def register_feedstock_token_handler(feedstock):
    if feedstock.endswith("-feedstock"):
        feedstock_name = feedstock[:-len("-feedstock")]
    else:
        feedstock_name = feedstock
    feedstock_url = "https://github.com/conda-forge/%s-feedstock.git" % feedstock_name

    error = False

    with tempfile.TemporaryDirectory() as tmpdir:
        with pushd(tmpdir):
            subprocess.run(
                ["git", "clone", "--depth=1", feedstock_url],
                check=True,
            )

            with pushd(feedstock_name):
                try:
                    subprocess.run(
                        ["conda-smithy", "generate-feedstock-token"],
                        check=True,
                    )
                    subprocess.run(
                        ["conda-smithy", "register-feedstock-token"],
                        check=True,
                    )
                finally:
                    try:
                        os.remove(os.path.expanduser(
                            "~/.conda_smithy/conda-forge_"
                            "%s_feedstock.token" % feedstock_name
                        ))
                    except Exception:
                        error = True
    return error


def _get_ac_api():
    """wrap this a function so we can more easily mock it when testing"""
    return get_server_api(token=os.environ["BINSTAR_TOKEN"])


def copy_feedstock_outputs(outputs, channel):
    """Copy outputs from one chanel to another.

    Parameters
    ----------
    outputs : dict
        A dictionary mapping each full qualified output in the conda index
        to a hash ("md5"), its name ("name"), and version ("version").
    channel : str
        The source and target channel to use. Pass "main" for the default
        channel.

    Returns
    -------
    copied : dict
        A dict keyed on the output name with True if the copy worked and False
        otherwise.
    """
    ac = _get_ac_api()

    copied = {o: False for o in outputs}

    for out_name, out in outputs.items():
        try:
            ac.copy(
                STAGING,
                out["name"],
                out["version"],
                basename=urllib.parse.quote(out_name, safe=""),
                to_owner=PROD,
                from_label=channel,
                to_label=channel,
            )
            copied[out_name] = True
        except BinstarError:
            pass

        if copied[out_name]:
            try:
                ac.remove_dist(
                    STAGING,
                    out["name"],
                    out["version"],
                    basename=urllib.parse.quote(out_name, safe=""),
                )
            except BinstarError:
                pass
    return copied


def _is_valid_output_hash(outputs):
    """Test if a set of outputs have valid hashes on the staging channel.

    Parameters
    ----------
    outputs : dict
        A dictionary mapping each full qualified output in the conda index
        to a hash ("md5"), its name ("name"), and version ("version").

    Returns
    -------
    valid : dict
        A dict keyed on output name with True if it is valid and False
        otherwise.
    """
    ac = get_server_api()

    valid = {o: False for o in outputs}

    for out_name, out in outputs.items():
        try:
            data = ac.distribution(
                STAGING,
                out["name"],
                out["version"],
                basename=urllib.parse.quote(out_name, safe=""),
            )
            valid[out_name] = hmac.compare_digest(data["md5"], out["md5"])
        except BinstarError:
            pass

    return valid


def is_valid_feedstock_output(project, outputs, register=True):
    """Test if feedstock outputs are valid. Optionally register them if they do not exist.

    Parameters
    ----------
    project : str
        The GitHub repo.
    outputs : list of str
        List of output names to validate.
    register : bool
        If True, attempt to register any outputs that do not exist by pushing
        the proper json blob to `output_repo`. Default is True.

    Returns
    -------
    valid : dict
        A dict keyed on output name with True if it is valid and False
        otherwise.
    """
    feedstock = project.replace("-feedstock", "")

    valid = {o: False for o in outputs}
    made_commit = False

    with tempfile.TemporaryDirectory() as tmpdir:
        _output_repo = OUTPUTS_REPO.replace("${GH_TOKEN}", os.environ["GH_TOKEN"])
        repo = git.Repo.clone_from(_output_repo, tmpdir, depth=1)

        for o in outputs:
            pth = os.path.join(tmpdir, "outputs", o + ".json")

            if not os.path.exists(pth):
                # no output exists, so we can add it
                valid[o] = True

                if register:
                    with open(pth, "w") as fp:
                        json.dump({"feedstocks": [feedstock]}, fp)
                    repo.index.add(pth)
                    repo.index.commit(
                        "added output %s for conda-forge/%s" % (o, project)
                    )
                    made_commit = True
            else:
                # make sure feedstock is ok
                with open(pth, "r") as fp:
                    data = json.load(fp)
                valid[o] = feedstock in data["feedstocks"]

        if register and made_commit:
            repo.remote().pull(rebase=True)
            repo.remote().push()

    return valid


def validate_feedstock_outputs(
    project,
    outputs,
    feedstock_token,
):
    """Validate feedstock outputs on the staging channel.

    Parameters
    ----------
    project : str
        The name of the feedstock.
    outputs : dict
        A dictionary mapping each full qualified output in the conda index
        to a hash ("md5"), its name ("name"), and version ("version").
    feedstock_token : str
        The secret token used to validate that this feedstock is who it says
        it is.

    Returns
    -------
    valid : dict
        A dict keyed on the keys in `outputs` with values True in the output
        is valid and False otherwise.
    """
    valid = {o: False for o in outputs}

    if not is_valid_feedstock_token(
        "conda-forge", project, feedstock_token, TOKENS_REPO
    ):
        return valid, ["invalid feedstock token"]

    valid_outputs = is_valid_feedstock_output(
        project,
        [o["name"] for _, o in outputs.items()],
        register=True,
    )

    valid_hashes = _is_valid_output_hash(outputs)

    errors = []
    for o in outputs:
        _errors = []
        if not valid_outputs[outputs[o]["name"]]:
            _errors.append(
                "output %s not allowed for conda-forge/%s" % (o, project)
            )
        if not valid_hashes[o]:
            _errors.append("output %s does not have a valid md5 checksum" % o)

        if len(_errors) > 0:
            errors.extend(_errors)
        else:
            valid[o] = True

    return valid, errors
