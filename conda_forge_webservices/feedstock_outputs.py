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
import binstar_client.errors

from conda_smithy.feedstock_tokens import is_valid_feedstock_token

from .utils import pushd

STAGING = "cf-staging"
PROD = "conda-forge"
OUTPUTS_REPO = "https://${GH_TOKEN}@github.com/conda-forge/feedstock-outputs.git"
TOKENS_REPO = "https://${GH_TOKEN}@github.com/conda-forge/feedstock-tokens.git"


def _run_git_command(*args):
    subprocess.run(
        " ".join(["git"] + list(args)),
        check=True,
        shell=True,
    )


def register_feedstock_token_handler(feedstock):
    """Generate and register feedstock tokens.

    Parameters
    ----------
    feedstock : str
        The name of the feedstock.

    Returns
    -------
    error : bool
        True if there is an error, False otherwise.
    """
    if feedstock.endswith("-feedstock"):
        feedstock_name = feedstock[:-len("-feedstock")]
    else:
        feedstock_name = feedstock

    feedstock_url = "https://github.com/conda-forge/%s-feedstock.git" % feedstock_name
    token_path = os.path.expanduser(
        "~/.conda-smithy/conda-forge_%s_feedstock.token" % feedstock_name
    )

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            with pushd(tmpdir):
                try:
                    _run_git_command("clone", "--depth=1", feedstock_url)
                except subprocess.CalledProcessError:
                    print("    could not clone the feedstock")
                    return True

                with pushd(feedstock_name + "-feedstock"):
                    try:
                        subprocess.run(
                            ["conda-smithy", "generate-feedstock-token"],
                            check=True,
                        )
                    except subprocess.CalledProcessError:
                        print("    could not generate feedstock token")
                        return True

                    try:
                        subprocess.run(
                            ["conda-smithy", "register-feedstock-token"],
                            check=True,
                        )
                    except subprocess.CalledProcessError:
                        print("    could not register feedstock token")
                        return True
    finally:
        try:
            os.remove(token_path)
        except Exception:
            print("    could not delete the feedstock token")
            return True

    return False


def _get_ac_api_prod():
    """wrap this a function so we can more easily mock it when testing"""
    return get_server_api(token=os.environ["PROD_BINSTAR_TOKEN"])


def _get_ac_api_staging():
    """wrap this a function so we can more easily mock it when testing"""
    return get_server_api(token=os.environ["STAGING_BINSTAR_TOKEN"])


def _dist_exists(ac, channel, name, version, basename):
    try:
        ac.distribution(
            channel,
            name,
            version,
            basename=urllib.parse.quote(basename, safe=""),
        )
        return True
    except binstar_client.errors.NotFound:
        return False


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
    ac_prod = _get_ac_api_prod()
    ac_staging = _get_ac_api_staging()

    copied = {o: False for o in outputs}

    for out_name, out in outputs.items():
        # if we already have it, then we mark it copied
        # this matches the old behavior where outputs are never
        # replaced once pushed
        if _dist_exists(ac_prod, PROD, out["name"], out["version"], out_name):
            copied[out_name] = True
        else:
            try:
                ac_prod.copy(
                    STAGING,
                    out["name"],
                    out["version"],
                    basename=urllib.parse.quote(out_name, safe=""),
                    to_owner=PROD,
                    from_label=channel,
                    to_label=channel,
                )
                copied[out_name] = True
                print("    copied:", out_name)
            except BinstarError:
                print("    did not copy:", out_name)
                pass

        if (
            copied[out_name]
            and _dist_exists(
                ac_staging,
                STAGING,
                out["name"],
                out["version"],
                out_name
            )
        ):
            try:
                ac_staging.remove_dist(
                    STAGING,
                    out["name"],
                    out["version"],
                    basename=urllib.parse.quote(out_name, safe=""),
                )
                print("    removed:", out_name)
            except BinstarError:
                print("    could not remove:", out_name)
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
            print("    did hash comp:", out_name)
        except BinstarError:
            print("    did not do hash comp:", out_name)
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
        with pushd(tmpdir):
            _run_git_command("clone", "--depth=1", OUTPUTS_REPO)

            with pushd("feedstock-outputs"):
                _run_git_command(
                    "remote",
                    "set-url",
                    "--push",
                    "origin",
                    OUTPUTS_REPO)

                for o in outputs:
                    pth = os.path.join("outputs", o + ".json")

                    if not os.path.exists(pth):
                        # no output exists, so we can add it
                        valid[o] = True

                        print("    does not exist|valid: %s|%s" % (o, valid[o]))
                        if register:
                            print("    registered:", o)
                            with open(pth, "w") as fp:
                                json.dump({"feedstocks": [feedstock]}, fp)
                            _run_git_command("add", pth)
                            _run_git_command(
                                "commit",
                                "-m",
                                "'added output %s for conda-forge/%s'" % (o, feedstock),
                            )
                            made_commit = True
                    else:
                        # make sure feedstock is ok
                        with open(pth, "r") as fp:
                            data = json.load(fp)
                        valid[o] = feedstock in data["feedstocks"]
                        print("    checked|valid: %s|%s" % (o, valid[o]))

                if register and made_commit:
                    _run_git_command("pull", "--commit", "--rebase")
                    _run_git_command("push")

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
    errors : list of str
        A list of any errors encountered.
    """
    valid = {o: False for o in outputs}

    if not is_valid_feedstock_token(
        "conda-forge", project, feedstock_token, TOKENS_REPO
    ):
        return valid, ["invalid feedstock token"]

    correctly_formatted = {o: True for o in outputs}

    errors = []

    for o, v in outputs.items():
        if "name" not in v:
            errors.append("output %s does not have a 'name' key" % o)
            correctly_formatted[o] = False
        if "version" not in v:
            errors.append("output %s does not have a 'version' key" % o)
            correctly_formatted[o] = False
        if "md5" not in v:
            errors.append("output %s does not have a 'md5' checksum key" % o)
            correctly_formatted[o] = False

    valid_outputs = is_valid_feedstock_output(
        project,
        [o["name"] for _, o in outputs.items() if "name" in o],
        register=True,
    )

    valid_hashes = _is_valid_output_hash(
        {o: v for o, v in outputs.items() if correctly_formatted[o]}
    )

    for o in outputs:
        if not correctly_formatted[o]:
            continue

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
