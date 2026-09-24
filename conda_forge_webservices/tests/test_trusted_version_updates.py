import subprocess

import pytest
import requests

from conda_forge_webservices import trusted_version_updates as tvu
from conda_forge_webservices.trusted_publishing import (
    GITHUB_ISSUER,
    TrustedPublishingError,
)

CONFIG = """\
conda_build_tool: rattler-build
trusted_publishers:
  - provider: github
    repository: DIRACGrid/DIRAC
    repository_owner_id: 1234
    workflow: deploy.yml
"""

CLAIMS = {
    "iss": GITHUB_ISSUER,
    "repository": "DIRACGrid/DIRAC",
    "job_workflow_ref": "DIRACGrid/DIRAC/.github/workflows/deploy.yml@refs/tags/v1",
    "run_id": "42",
}

SHA = "a" * 40


class _Response:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class _Feedstock:
    """conda-forge/lbenv-feedstock as git and raw see it, counting lookups.

    `configs` maps a commit to the text of its conda-forge.yml, or to an
    exception or a status code for raw to answer with.
    """

    def __init__(self):
        self.exists = True
        self.default_branch = "main"
        self.branches = {"main": SHA}
        self.configs = {SHA: CONFIG}
        self.git_failure = None
        self.lookups = []

    def ls_remote(self, feedstock, branch):
        self.lookups.append(("ls-remote", feedstock, branch))
        if self.git_failure is not None:
            raise self.git_failure
        if feedstock != "lbenv-feedstock" or not self.exists:
            raise tvu.BadRequest(404, f"there is no conda-forge/{feedstock}")
        lines = []
        # a repository without its default branch has no HEAD to name
        if self.default_branch in self.branches:
            head = self.branches[self.default_branch]
            lines += [f"ref: refs/heads/{self.default_branch}\tHEAD", f"{head}\tHEAD"]
        # as ls-remote does, anything whose name ends with the pattern
        for name, sha in self.branches.items():
            if branch is not None and (name == branch or name.endswith("/" + branch)):
                lines.append(f"{sha}\trefs/heads/{name}")
        return "\n".join(lines) + "\n"

    def get(self, url, timeout):
        self.lookups.append(("raw", url))
        prefix = "https://raw.githubusercontent.com/conda-forge/lbenv-feedstock/"
        assert url.startswith(prefix) and url.endswith("/conda-forge.yml"), url
        answer = self.configs.get(url[len(prefix) :].split("/")[0], 404)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            return _Response(answer)
        return _Response(200, answer)


@pytest.fixture(autouse=True)
def _fresh():
    tvu._RECENT.clear()
    tvu._CONFIGS.clear()
    yield
    tvu._RECENT.clear()
    tvu._CONFIGS.clear()


@pytest.fixture
def feedstock(monkeypatch):
    """conda-forge/lbenv-feedstock, whose job DIRACGrid/DIRAC may publish."""
    feedstock = _Feedstock()
    monkeypatch.setattr(tvu, "_ls_remote", feedstock.ls_remote)
    monkeypatch.setattr(tvu.requests, "get", feedstock.get)
    monkeypatch.setattr(tvu, "verify_token", lambda token: CLAIMS)
    monkeypatch.setattr(tvu, "authorize", lambda claims, publishers: publishers[0])
    return feedstock


def _refuse(message, retryable=False):
    def _raise(*args):
        raise TrustedPublishingError(message, retryable=retryable)

    return _raise


@pytest.mark.parametrize(
    "version",
    [
        "1.2.3",
        "2026.9.10",
        "1.2.3rc1",
        "1.0.0.post1",
        "1!2.0.0",
        "v2.4.0",
        "1.2.3+cuda",
        "1234",
        "2024g",
        # a feedstock that trusts a publisher trusts the version it names, so
        # any conda version is taken, even one GitHub's archive URLs would
        # also read as a commit or a branch
        "2024a",
        "1234567",
        "ea06d64",
        "main",
    ],
)
def test_any_conda_version_is_taken(version):
    assert tvu.valid_version(version)
    tvu._check_request("lbenv-feedstock", version, None)


@pytest.mark.parametrize(
    "version",
    [
        None,
        5,
        "",
        # conda's parser strips these, and takes a glob and a dash
        " 1.2.3",
        "1.2.3\n",
        "1*",
        "1.0.0-rc1",
        # it refuses these itself
        "1..2",
        "1.",
        ".1",
        # and none of these may reach the recipe or its source.url
        '1.2"',
        "{{ version }}",
        "${version}",
        "$(whoami)",
        "1.2.3;rm -rf /",
        "../../etc/passwd",
        "1`2",
        "1" * 65,
        # the updater reads these as asking it to find the newest version
        "null",
        "None",
        "NULL",
    ],
)
def test_versions_we_refuse(version):
    assert not tvu.valid_version(version)


@pytest.mark.parametrize(
    "feedstock", ["lbenv-feedstock", "python-feedstock", "r-base_1-feedstock"]
)
def test_feedstock_names_we_accept(feedstock):
    assert tvu.FEEDSTOCK.fullmatch(feedstock)


@pytest.mark.parametrize(
    "feedstock",
    [
        "staged-recipes",
        "lbenv",
        "../lbenv-feedstock",
        "conda-forge/lbenv-feedstock",
        "",
        "lbenv-feedstock\n",
    ],
)
def test_feedstock_names_we_refuse(feedstock):
    assert not tvu.FEEDSTOCK.fullmatch(feedstock)


@pytest.mark.parametrize(
    "header,token",
    [
        ("Bearer abc.def.ghi", "abc.def.ghi"),
        ("bearer abc.def.ghi", "abc.def.ghi"),
        ("Bearer  abc.def.ghi ", "abc.def.ghi"),
        # tornado strips the trailing space of an empty "Bearer "
        ("Bearer", ""),
        ("Bearer ", ""),
        ("", ""),
        ("Basic dXNlcjpwYXNz", ""),
        ("abc.def.ghi", ""),
    ],
)
def test_the_token_is_read_from_a_bearer_header(header, token):
    assert tvu.bearer_token(header) == token


@pytest.mark.parametrize(
    "branch", ["main", "2.4.x", "release/2.4", "v2_x", "feature/a-b.c"]
)
def test_branch_names_we_accept(branch):
    assert tvu.valid_branch(branch)


@pytest.mark.parametrize(
    "branch",
    [
        "",
        "x/../../../attacker/their-repo/main",
        "..",
        "a..b",
        "../main",
        "main/..",
        "/main",
        "main/",
        "a//b",
        ".hidden",
        "a/.hidden",
        "main.lock",
        "main.",
        "refs/heads/../x",
        "a b",
        "a~1",
        "a^",
        "a:b",
        "a@{1}",
        "a\\b",
        "x" * 101,
        "main\n",
        "release/2.4\n",
    ],
)
def test_branch_names_we_refuse(branch):
    assert not tvu.valid_branch(branch)


def test_a_request_without_a_token_is_refused(feedstock):
    with pytest.raises(tvu.BadRequest, match="no identity token") as err:
        tvu.authorize_request("", "lbenv-feedstock", "1.2.3", None)
    assert err.value.status == 401
    assert feedstock.lookups == []


@pytest.mark.parametrize(
    "name,version,branch",
    [
        ("lbenv", "1.2.3", None),
        ("lbenv-feedstock", "1.0.0-rc1", None),
        ("lbenv-feedstock", "1..2", None),
        ("lbenv-feedstock", "null", None),
        ("lbenv-feedstock", "{{ version }}", None),
        ("lbenv-feedstock", "1.2.3", "../main"),
        # would reach another repository's config through dot segments
        ("numpy-feedstock", "1.2.3", "x/../../../attacker/their-repo/main"),
        ("lbenv-feedstock", "1.2.3", "main.lock"),
        ("lbenv-feedstock", "1.2.3", "--upload-pack=x"),
        ("lbenv-feedstock", None, None),
        (None, "1.2.3", None),
    ],
)
def test_a_request_we_will_not_read_is_refused(feedstock, name, version, branch):
    with pytest.raises(tvu.BadRequest) as err:
        tvu.authorize_request("token", name, version, branch)
    assert err.value.status == 400
    assert feedstock.lookups == [], "looked up a request that was never valid"


def test_a_token_that_is_not_genuine_is_refused_before_any_lookup(
    feedstock, monkeypatch
):
    monkeypatch.setattr(tvu, "verify_token", _refuse("the token is not valid"))
    with pytest.raises(tvu.BadRequest, match="not valid") as err:
        tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)
    assert err.value.status == 403
    assert feedstock.lookups == [], "looked up on behalf of a caller who proved nothing"


def test_a_retryable_refusal_says_when_to_retry(feedstock, monkeypatch):
    monkeypatch.setattr(
        tvu, "verify_token", _refuse("keys not loaded yet", retryable=True)
    )
    with pytest.raises(tvu.BadRequest) as err:
        tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)
    assert err.value.status == 503
    assert err.value.retry_after == tvu.RETRY_AFTER


def test_the_default_branch_is_resolved_and_read_at_its_commit(feedstock):
    request = tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)

    assert (request.default_branch, request.branch, request.sha) == (
        "main",
        "main",
        SHA,
    )
    assert request.claims is CLAIMS
    # read at the commit the branch pointed to, which is where the pull request
    # starts, and only from conda-forge/<feedstock>
    assert feedstock.lookups == [
        ("ls-remote", "lbenv-feedstock", None),
        (
            "raw",
            f"https://raw.githubusercontent.com/conda-forge/lbenv-feedstock/{SHA}"
            "/conda-forge.yml",
        ),
    ]


def test_a_branch_is_only_ever_a_branch_of_the_feedstock(feedstock):
    """A name shaped like a commit, or the end of another ref, is not one."""
    feedstock.branches = {"main": SHA, "2.4.x": "b" * 40, "backport/2.4.x": "c" * 40}
    feedstock.configs = {SHA: CONFIG, "b" * 40: CONFIG, "c" * 40: CONFIG}

    request = tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", "2.4.x")
    assert (request.default_branch, request.branch, request.sha) == (
        "main",
        "2.4.x",
        "b" * 40,
    )

    for name in ["c" * 40, "x"]:
        with pytest.raises(tvu.BadRequest, match="has no branch") as err:
            tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", name)
        assert err.value.status == 404


@pytest.mark.parametrize(
    "setup,message",
    [
        (lambda f: setattr(f, "exists", False), "there is no conda-forge/lbenv"),
        (lambda f: f.branches.clear(), "has no default branch"),
        (lambda f: f.configs.clear(), "has no conda-forge.yml on main"),
    ],
)
def test_what_is_missing_is_not_found(feedstock, setup, message):
    setup(feedstock)
    with pytest.raises(tvu.BadRequest, match=message) as err:
        tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)
    assert err.value.status == 404


@pytest.mark.parametrize(
    "config,message",
    [
        ("- a\n- b\n", "is not a mapping"),
        ("just text\n", "is not a mapping"),
        ("a: [\n", "does not parse"),
    ],
)
def test_a_config_we_cannot_read_says_so(feedstock, config, message):
    feedstock.configs = {SHA: config}
    with pytest.raises(tvu.BadRequest, match=message) as err:
        tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)
    assert err.value.status == 422


@pytest.mark.parametrize(
    "failure",
    [502, 503, 429, requests.ConnectionError("reset"), requests.Timeout("slow")],
)
def test_raw_being_unavailable_is_retryable(feedstock, failure):
    feedstock.configs = {SHA: failure}
    with pytest.raises(tvu.BadRequest) as err:
        tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)
    assert err.value.status == 503
    assert err.value.retry_after == tvu.RETRY_AFTER


def test_git_being_unavailable_is_retryable(feedstock):
    feedstock.git_failure = tvu._unavailable(RuntimeError("could not resolve host"))
    with pytest.raises(tvu.BadRequest) as err:
        tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)
    assert err.value.status == 503


def test_a_feedstock_that_has_not_opted_in_says_how_to(feedstock):
    feedstock.configs = {SHA: "bot:\n  automerge: true\n"}
    with pytest.raises(tvu.BadRequest, match="lists no trusted publishers") as err:
        tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)
    assert err.value.status == 403
    assert tvu.DOCS in err.value.message


def test_what_a_branch_resolves_to_is_kept_for_a_while(feedstock, monkeypatch):
    monkeypatch.setattr(tvu, "authorize", _refuse("matches no trusted publisher"))
    for _ in range(3):
        with pytest.raises(tvu.BadRequest):
            tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)
    assert len(feedstock.lookups) == 2

    for _ in range(3):
        with pytest.raises(tvu.BadRequest, match="has no branch"):
            tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", "gone")
    assert len(feedstock.lookups) == 3, "a missing branch was looked up again"


def test_a_kept_refusal_is_raised_afresh_each_time(feedstock):
    """The same exception raised again grows its traceback and keeps frames."""
    feedstock.branches.clear()
    raised = []
    for _ in range(3):
        with pytest.raises(tvu.BadRequest) as err:
            tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)
        raised.append(err.value)
    assert len({id(e) for e in raised}) == 3
    assert all(e.status == 404 and "has no default branch" in e.message for e in raised)


def test_a_token_that_matches_nothing_is_refused(feedstock, monkeypatch):
    monkeypatch.setattr(tvu, "authorize", _refuse("matches no trusted publisher"))
    with pytest.raises(tvu.BadRequest, match="matches no trusted publisher") as err:
        tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)
    assert err.value.status == 403


def test_an_authorized_request_is_answered_once_per_cooldown(feedstock):
    tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)

    with pytest.raises(tvu.BadRequest, match="less than") as err:
        tvu.authorize_request("token", "lbenv-feedstock", "1.2.4", None)
    assert err.value.status == 429
    assert err.value.retry_after == tvu.COOLDOWN


def test_the_cooldown_is_per_branch(feedstock):
    """A release that updates two branches does not collide with itself."""
    feedstock.branches = {"main": SHA, "2.4.x": SHA}
    tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)
    tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", "2.4.x")


def test_a_refused_token_does_not_spend_the_cooldown(feedstock, monkeypatch):
    monkeypatch.setattr(tvu, "authorize", _refuse("nope"))
    with pytest.raises(tvu.BadRequest):
        tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)

    monkeypatch.setattr(tvu, "authorize", lambda claims, publishers: None)
    tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)


def test_the_cooldown_does_not_spend_the_token(feedstock, monkeypatch):
    """A job on GitLab gets one token, so a 429 must leave it usable."""
    tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)

    spent = []
    monkeypatch.setattr(
        tvu, "authorize", lambda claims, publishers: spent.append(claims)
    )
    with pytest.raises(tvu.BadRequest) as err:
        tvu.authorize_request("token", "lbenv-feedstock", "1.2.4", None)
    assert err.value.status == 429
    assert spent == []


def test_releasing_the_cooldown_lets_the_branch_be_asked_for_again(feedstock):
    request = tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)
    tvu.release_cooldown(request)
    tvu.authorize_request("token", "lbenv-feedstock", "1.2.3", None)


@pytest.fixture
def bare(tmp_path, monkeypatch):
    """A real repository for git ls-remote to read, standing in for GitHub."""

    def git(*args, cwd=tmp_path):
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
        ).stdout.strip()

    work = tmp_path / "work"
    git("init", "-q", "-b", "main", str(work))
    (work / "conda-forge.yml").write_text(CONFIG)
    git("add", "conda-forge.yml", cwd=work)
    git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-qm",
        "init",
        cwd=work,
    )
    git("branch", "2.4.x", cwd=work)
    # ends in refs/heads/main as well, and points somewhere else
    git("checkout", "-qb", "x/refs/heads/main", cwd=work)
    git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "look-alike",
        cwd=work,
    )
    git("checkout", "-q", "main", cwd=work)
    git("clone", "-q", "--bare", str(work), str(tmp_path / "lbenv-feedstock.git"))

    monkeypatch.setattr(tvu, "REMOTE", str(tmp_path / "{}.git"))
    return git("rev-parse", "main", cwd=work)


def test_ls_remote_finds_the_default_branch_and_the_branch_named(bare):
    default_branch, refs = tvu._refs(tvu._ls_remote("lbenv-feedstock", "2.4.x"))
    assert default_branch == "main"
    assert refs["refs/heads/2.4.x"] == bare


def test_the_default_branch_is_resolved_by_real_git(bare, monkeypatch):
    monkeypatch.setattr(
        tvu.requests, "get", lambda url, timeout: _Response(200, CONFIG)
    )
    default_branch, branch, sha, publishers = tvu._fetch_config("lbenv-feedstock", None)
    assert (default_branch, branch, sha) == ("main", "main", bare)
    assert len(publishers) == 1


def test_ls_remote_of_a_feedstock_that_is_not_there_is_not_found(bare):
    with pytest.raises(tvu.BadRequest) as err:
        tvu._ls_remote("nope-feedstock", None)
    assert err.value.status == 404


def test_a_branch_ending_like_another_resolves_to_itself(bare, monkeypatch):
    """refs/heads/x/refs/heads/main ends in refs/heads/main as well."""
    _, refs = tvu._refs(tvu._ls_remote("lbenv-feedstock", "main"))
    lookalike = refs["refs/heads/x/refs/heads/main"]
    # git did answer with both, so which one is taken is what is tested
    assert lookalike != bare

    monkeypatch.setattr(
        tvu.requests, "get", lambda url, timeout: _Response(200, CONFIG)
    )
    request = tvu._fetch_config("lbenv-feedstock", "main")
    assert request[1:3] == ("main", bare)


REQUEST = tvu.Authorized(
    feedstock="lbenv-feedstock",
    version="1.2.3",
    default_branch="main",
    branch="2.4.x",
    sha="b" * 40,
    # the branch in it is whatever whoever pushed to the publisher chose
    claims=dict(
        CLAIMS,
        job_workflow_ref=(
            "DIRACGrid/DIRAC/.github/workflows/deploy.yml@refs/heads/"
            "@here **bold** `tick`"
        ),
    ),
)

RUN = "https://github.com/conda-forge/conda-forge-webservices/actions/runs/9"


class _PullRequest:
    number = 7
    html_url = "https://github.com/conda-forge/lbenv-feedstock/pull/7"

    def __init__(self):
        self.comments = []

    def create_issue_comment(self, body):
        self.comments.append(body)


@pytest.fixture
def opening(monkeypatch):
    """Record what open_version_update_pr asks of GitHub and git."""
    seen = {"pr": _PullRequest()}

    class _UserGitHub:
        def __init__(self, auth):
            seen["auth"] = auth

        def get_repo(self, name):
            seen["repo"] = name
            return "upstream repo"

    class _Branch:
        def __init__(self, gh, org, name, default_branch, branch_name, start_point):
            seen["admin_feedstock_branch"] = (
                gh,
                org,
                name,
                default_branch,
                start_point,
            )
            seen["branch_name"] = branch_name

        def __enter__(self):
            return "git repo", "conda-forge-admin"

        def __exit__(self, *args):
            return False

    def _open(repo, git_repo, forked_user, branch_name, **kwargs):
        seen["open_admin_pr"] = kwargs
        return seen["pr"]

    def _dummy(git_repo, skip_ci):
        seen["skip_ci"] = skip_ci

    monkeypatch.setenv("GH_TOKEN", "conda-forge-admin's token")
    monkeypatch.setattr(tvu.github, "Github", _UserGitHub)
    monkeypatch.setattr(tvu, "admin_feedstock_branch", _Branch)
    monkeypatch.setattr(tvu, "open_admin_pr", _open)
    monkeypatch.setattr(tvu, "make_rerender_dummy_commit", _dummy)
    monkeypatch.setattr(tvu, "dispatch_version_update", lambda *a, **kw: RUN)
    return seen


def test_the_pull_request_is_opened_as_conda_forge_admin(opening):
    data = tvu.open_version_update_pr(REQUEST)

    assert data == {
        "pull_request": 7,
        "url": _PullRequest.html_url,
        "version_update_started": True,
        "version_update_run": RUN,
    }
    # a real account, since the app cannot hold a fork
    assert opening["auth"].token == "conda-forge-admin's token"
    gh, org, name, default_branch, start_point = opening["admin_feedstock_branch"]
    assert isinstance(gh, tvu.github.Github)
    # the fork is synced to the real default branch, and the pull request cut
    # from the commit whose conda-forge.yml was read
    assert (org, name, default_branch, start_point) == (
        "conda-forge",
        "lbenv-feedstock",
        "main",
        "b" * 40,
    )
    assert opening["open_admin_pr"]["base"] == "2.4.x"
    assert opening["skip_ci"] is True
    # so that the version updater can tell it from a command's pull request
    assert tvu.TRUSTED_PUBLISHING_MARKER in opening["open_admin_pr"]["body"]


def test_the_requester_cannot_mention_or_format(opening):
    tvu.open_version_update_pr(REQUEST)
    body = opening["open_admin_pr"]["body"]

    requester = body.split(" asked for")[0].rsplit("\n", 1)[1]
    assert requester.startswith("`") and "`tick`" not in requester
    assert "@here **bold** 'tick'" in requester
    assert "(https://github.com/DIRACGrid/DIRAC/actions/runs/42)" in requester


@pytest.mark.parametrize("update", ["fails", "raises"])
def test_a_version_update_that_does_not_start_is_said(opening, monkeypatch, update):
    def _dispatch(*args, **kwargs):
        if update == "raises":
            raise RuntimeError("dispatch failed")
        return None

    monkeypatch.setattr(tvu, "dispatch_version_update", _dispatch)
    data = tvu.open_version_update_pr(REQUEST)

    # the pull request exists either way, so the caller is told about it
    assert data["pull_request"] == 7
    assert data["version_update_started"] is False
    assert data["version_update_run"] is None
    comment = opening["pr"].comments[0]
    assert "`1.2.3`" in comment
    # it cannot suggest a command, since anyone can post one
    assert "@conda-forge-admin" not in comment


def test_a_live_test_can_dispatch_at_its_branch(opening, monkeypatch):
    dispatched = {}
    monkeypatch.setattr(
        tvu, "dispatch_version_update", lambda *a, **kw: dispatched.update(kw) or RUN
    )
    monkeypatch.setenv(tvu.DISPATCH_REF_ENV, "feat/x")
    tvu.open_version_update_pr(REQUEST)
    assert dispatched["dispatch_ref"] == "feat/x"

    monkeypatch.delenv(tvu.DISPATCH_REF_ENV)
    tvu.open_version_update_pr(REQUEST)
    assert dispatched["dispatch_ref"] is None
