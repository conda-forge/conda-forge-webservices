"""
To run these tests

1. start the web server locally via

   python -u -m conda_forge_webservices.webapp --local

2. Make sure you have a github token in the GH_TOKEN environment variable.

3. Make sure you have the STAGING_BINSTAR_TOKEN and PROD_BINSTAR_TOKEN environment
   variables defined.

4. Run these tests via pytest -vvs test_cfep13_copy.py
"""

import os
import tempfile
import json
import subprocess
import uuid
import glob
import hashlib
import pprint
import contextlib
import shutil
import urllib
import time
import tqdm
from typing import Optional

import requests
from binstar_client import BinstarError
import binstar_client.errors

import pytest

from conda_forge_webservices.utils import pushd, with_action_url
from conda_forge_webservices.feedstock_outputs import (
    _get_ac_api_prod,
    _get_ac_api_staging,
)

OUTPUTS_REPO = (
    "https://x-access-token:${GH_TOKEN}@github.com/conda-forge/" "feedstock-outputs.git"
)

try:
    token_path = "${HOME}/.conda-smithy/conda-forge_staged-recipes.token"
    with open(os.path.expandvars(token_path), "r") as fp:
        sr_token = fp.read().strip()

    headers: Optional[dict] = {
        "FEEDSTOCK_TOKEN": sr_token,
    }
except Exception:
    headers = None


def _run_git_command(*args):
    subprocess.run(
        " ".join(["git"] + list(args)),
        check=True,
        shell=True,
    )


def _clone_and_remove(repo, file_to_remove):
    with tempfile.TemporaryDirectory() as tmpdir:
        with pushd(tmpdir):
            _run_git_command("clone", "--depth=1", repo)

            with pushd(os.path.split(repo)[1].replace(".git", "")):
                _run_git_command(
                    "remote",
                    "set-url",
                    "--push",
                    "origin",
                    repo,
                )
                if os.path.exists(file_to_remove):
                    print("    repo %s: removed file %s" % (repo, file_to_remove))
                    _run_git_command("rm", file_to_remove)
                    msg = with_action_url(f"removed {file_to_remove} for testing")
                    _run_git_command(
                        "commit",
                        "-m",
                        f"'{msg}'",
                    )
                    _run_git_command("pull", "--rebase", "--commit")
                    _run_git_command("push")


def _build_recipe(uid):
    os.makedirs("recipe", exist_ok=True)

    with open("recipe/meta.yaml", "w") as fp:
        fp.write("""\
{% set version = datetime.datetime.utcnow().strftime('%Y.%m.%d.%H.%M.%S') %}
{% set name = "blah-" ~ uuid %}

package:
  name: {{ name|lower }}
  version: {{ version }}

source:
  path: .

build:
  number: 0
  noarch: python

requirements:
  host:
    - python
    - pip
  run:
    - python

test:
  commands:
    - echo "works!"

about:
  home: https://github.com/conda-forge/conda-forge-webservices
  license: BSD-3-Clause
  license_family: BSD
  license_file: LICENSE
  summary: testing package for the conda forge webservices

extra:
  recipe-maintainers:
    - conda-forge/core
""")

    with open("recipe/LICENSE", "w") as fp:
        fp.write("""\
BSD 3-clause
Copyright (c) conda-forge
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation and/or
   other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software without
   specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA,
OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.
""")

    with open("recipe/conda_build_config.yaml", "w") as fp:
        fp.write(
            """\
uuid:
  - %s
"""
            % uid
        )
    subprocess.run(
        "mkdir -p built_dists "
        "&& rm -rf built_dists/* "
        '&& export CONDA_BLD_PATH="$(pwd)/built_dists" '
        "&& conda-build recipe",
        check=True,
        shell=True,
    )


def _split_pkg(pkg):
    if pkg.endswith(".tar.bz2"):
        pkg = pkg[: -len(".tar.bz2")]
    elif pkg.endswith(".conda"):
        pkg = pkg[: -len(".conda")]
    else:
        raise RuntimeError("Can only process packages that end in .tar.bz2 or .conda!")
    plat, pkg_name = pkg.split(os.path.sep)
    name_ver, build = pkg_name.rsplit("-", 1)
    name, ver = name_ver.rsplit("-", 1)
    return plat, name, ver, build


def _compute_local_info(dist, croot, hash_type):
    h = getattr(hashlib, hash_type)()

    with open(os.path.join(croot, dist), "rb") as fp:
        chunk = 0
        while chunk != b"":
            chunk = fp.read(1024)
            h.update(chunk)

    md5 = h.hexdigest()

    return {dist: md5}


@contextlib.contextmanager
def _get_temp_token(token):
    dn = tempfile.mkdtemp()
    fn = os.path.join(dn, "binstar.token")
    with open(fn, "w") as fh:
        fh.write(token)
    yield fn
    shutil.rmtree(dn)


def _remove_dist(ac, channel, dist):
    _, name, version, _ = _split_pkg(dist)
    try:
        ac.remove_dist(
            channel,
            name,
            version,
            basename=urllib.parse.quote(dist, safe=""),
        )
        return True
    except BinstarError:
        return False


def _dist_exists(ac, channel, dist):
    _, name, version, _ = _split_pkg(dist)
    try:
        ac.distribution(
            channel,
            name,
            version,
            basename=urllib.parse.quote(dist, safe=""),
        )
        return True
    except binstar_client.errors.NotFound:
        return False


@pytest.mark.skipif(headers is None, reason="No feedstock token for testing!")
def test_feedstock_outputs_copy_works():
    uid = uuid.uuid4().hex[0:6]

    print("\n=========================================================")
    print("running conda build")
    print("=========================================================", flush=True)
    _build_recipe(uid)
    dists = glob.glob("built_dists/noarch/blah-*.tar.bz2")
    assert len(dists) == 1
    dists = [os.path.relpath(dist, start="built_dists") for dist in dists]

    ac_prod = _get_ac_api_prod()
    ac_staging = _get_ac_api_staging()

    for hash_type in [None, "md5", "sha256"]:
        print("\n=========================================================")
        print("computing dist info")
        print("=========================================================", flush=True)
        outputs = {}
        for dist in dists:
            outputs.update(_compute_local_info(dist, "built_dists", hash_type or "md5"))

        print("outputs:", pprint.pformat(outputs))

        try:
            print("\n=========================================================")
            print("uploading to cf-staging")
            print(
                "=========================================================",
                flush=True,
            )
            with _get_temp_token(os.environ["STAGING_BINSTAR_TOKEN"]) as fn:
                for output in outputs:
                    pth = os.path.join("built_dists", output)
                    subprocess.run(
                        " ".join(
                            [
                                "anaconda",
                                "--quiet",
                                "-t",
                                fn,
                                "upload",
                                pth,
                                "--user=cf-staging",
                                "--channel=main",
                            ]
                        ),
                        check=True,
                        shell=True,
                    )

            print("\n=========================================================")
            print("sleeping for 10 seconds")
            print(
                "=========================================================",
                flush=True,
            )
            for _ in tqdm.trange(10):
                time.sleep(1)

            print("\n=========================================================")
            print("checking that dists exist on staging")
            print(
                "=========================================================",
                flush=True,
            )
            for dist in outputs:
                assert _dist_exists(ac_staging, "cf-staging", dist)
                print("    cf-staging: dist %s exists" % dist)

            print("\n=========================================================")
            print("making copy call to admin server")
            print(
                "=========================================================",
                flush=True,
            )
            json_data = {
                "feedstock": "staged-recipes",
                "outputs": outputs,
                "channel": "main",
            }
            if hash_type is not None:
                json_data["hash_type"] = hash_type
            r = requests.post(
                "http://127.0.0.1:5000/feedstock-outputs/copy",
                headers=headers,
                json=json_data,
            )
            assert r.status_code == 200
            print("    response:", pprint.pformat(r.json()))

            print("\n=========================================================")
            print("sleeping for 10 seconds for copy")
            print(
                "=========================================================",
                flush=True,
            )
            for _ in tqdm.trange(10):
                time.sleep(1)

            print("\n=========================================================")
            print("checking that dists exist on prod")
            print(
                "=========================================================",
                flush=True,
            )
            for dist in outputs:
                assert _dist_exists(ac_prod, "conda-forge", dist)
                print("    conda-forge: dist %s exists" % dist)

            print("\n=========================================================")
            print("checking the new outputs")
            print(
                "=========================================================",
                flush=True,
            )
            _fname = "outputs/b/l/a/blah-%s.json" % uid
            with tempfile.TemporaryDirectory() as tmpdir:
                with pushd(tmpdir):
                    _run_git_command("clone", "--depth=1", OUTPUTS_REPO)

                    with pushd(os.path.split(OUTPUTS_REPO)[1].replace(".git", "")):
                        assert os.path.exists(_fname)
                        with open(_fname, "r") as fp:
                            data = json.load(fp)
                            assert data["feedstocks"] == ["staged-recipes"]

        finally:
            print("\n=========================================================")
            print("cleaning up repos and dists")
            print(
                "=========================================================",
                flush=True,
            )
            for dist in outputs:
                if _dist_exists(ac_staging, "cf-staging", dist):
                    if _remove_dist(ac_staging, "cf-staging", dist):
                        print("cf-staging: removed %s" % dist)
                if _dist_exists(ac_prod, "conda-forge", dist):
                    if _remove_dist(ac_prod, "conda-forge", dist):
                        print("cond-forge: removed %s" % dist)

            _clone_and_remove(OUTPUTS_REPO, "outputs/b/l/a/blah-%s.json" % uid)
            print(" ")
