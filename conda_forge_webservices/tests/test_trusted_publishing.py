import datetime

import jwt
import pytest
import requests
from conda_smithy.schema import GitHubTrustedPublisher, GitLabTrustedPublisher
from cryptography.hazmat.primitives.asymmetric import rsa

from conda_forge_webservices import trusted_publishing as tp

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

GITHUB_CLAIMS = {
    "iss": tp.GITHUB_ISSUER,
    "sub": "repo:DIRACGrid/DIRAC:ref:refs/tags/v9.1.19",
    "repository": "DIRACGrid/DIRAC",
    "repository_owner": "DIRACGrid",
    "repository_owner_id": "1234",
    "workflow_ref": "DIRACGrid/DIRAC/.github/workflows/deploy.yml@refs/tags/v9.1.19",
    "job_workflow_ref": (
        "DIRACGrid/DIRAC/.github/workflows/deploy.yml@refs/tags/v9.1.19"
    ),
    "ref": "refs/tags/v9.1.19",
    "ref_type": "tag",
    "run_id": "35107909883",
}

# the entries pin the ids as numbers while the claims arrive as strings
GITHUB_PUBLISHER = GitHubTrustedPublisher(
    provider="github",
    repository="DIRACGrid/DIRAC",
    repository_owner_id=1234,
    workflow="deploy.yml",
)

GITLAB_PUBLISHER = GitLabTrustedPublisher(
    provider="gitlab",
    project_path="lhcb-core/LbEnv",
    namespace_id=4321,
    ref_type="tag",
    ref_protected=True,
)

GITLAB_CLAIMS = {
    "iss": GITLAB_PUBLISHER.url,
    "sub": "project_path:lhcb-core/LbEnv:ref_type:tag:ref:v2.4.0",
    "project_path": "lhcb-core/LbEnv",
    "namespace_path": "lhcb-core",
    "namespace_id": "4321",
    "ref": "v2.4.0",
    "ref_type": "tag",
    "ref_protected": "true",
}


def _make_token(claims, *, key=KEY, algorithm="RS256", lifetime_minutes=5, **overrides):
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "aud": tp.AUDIENCE,
        "iat": now,
        "exp": now + datetime.timedelta(minutes=lifetime_minutes),
        **claims,
        **overrides,
    }
    return jwt.encode(payload, key, algorithm=algorithm)


@pytest.fixture
def trusted_key(monkeypatch):
    """Serve our own public key as the signing key for every issuer."""
    monkeypatch.setattr(tp, "_signing_key", lambda issuer, token: KEY.public_key())


def test_github_token_authorizes(trusted_key):
    publisher, claims = tp.authorize(_make_token(GITHUB_CLAIMS), [GITHUB_PUBLISHER])
    assert publisher is GITHUB_PUBLISHER
    assert claims["repository"] == "DIRACGrid/DIRAC"


def test_gitlab_token_authorizes(trusted_key):
    publisher, claims = tp.authorize(_make_token(GITLAB_CLAIMS), [GITLAB_PUBLISHER])
    assert publisher is GITLAB_PUBLISHER
    assert claims["project_path"] == "lhcb-core/LbEnv"


def test_first_matching_publisher_wins(trusted_key):
    publisher, _ = tp.authorize(
        _make_token(GITHUB_CLAIMS), [GITLAB_PUBLISHER, GITHUB_PUBLISHER]
    )
    assert publisher is GITHUB_PUBLISHER


def test_an_entry_we_cannot_read_is_skipped_not_fatal():
    publishers = tp.parse_publishers(
        [
            {"provider": "bitbucket", "repository": "a/b"},
            # an id is required, so an entry without one never reaches matching
            {"provider": "github", "repository": "a/b", "workflow": "x.yml"},
            GITHUB_PUBLISHER.model_dump(),
        ]
    )
    assert publishers == [GITHUB_PUBLISHER]


def test_publishers_are_read_as_a_feedstock_writes_them():
    publishers = tp.parse_publishers(
        [
            {
                "provider": "github",
                "repository": "DIRACGrid/DIRAC",
                "repository_owner_id": 694842,
                "workflow": "deploy.yml",
            },
            {
                "provider": "gitlab",
                "url": "https://gitlab.cern.ch",
                "project_path": "lhcb-core/LbEnv",
                "namespace_id": 783,
                "ref_type": "tag",
                "ref_protected": True,
            },
        ]
    )
    assert [tp.issuer_of(publisher) for publisher in publishers] == [
        tp.GITHUB_ISSUER,
        "https://gitlab.cern.ch",
    ]


def test_no_entries_at_all_reads_as_none():
    assert tp.parse_publishers(None) == []


def test_token_for_another_audience_is_rejected(trusted_key):
    token = _make_token(GITHUB_CLAIMS, aud="pypi")
    with pytest.raises(tp.TrustedPublishingError, match="not valid"):
        tp.authorize(token, [GITHUB_PUBLISHER])


def test_expired_token_is_rejected(trusted_key):
    token = _make_token(GITHUB_CLAIMS, lifetime_minutes=-5)
    with pytest.raises(tp.TrustedPublishingError, match="not valid"):
        tp.authorize(token, [GITHUB_PUBLISHER])


def test_token_signed_by_another_key_is_rejected(trusted_key):
    token = _make_token(GITHUB_CLAIMS, key=OTHER_KEY)
    with pytest.raises(tp.TrustedPublishingError, match="not valid"):
        tp.authorize(token, [GITHUB_PUBLISHER])


def test_unsigned_token_is_rejected(trusted_key):
    token = _make_token(GITHUB_CLAIMS, key=None, algorithm="none")
    with pytest.raises(tp.TrustedPublishingError, match="not valid"):
        tp.authorize(token, [GITHUB_PUBLISHER])


def test_token_missing_a_required_claim_is_rejected(trusted_key):
    claims = {k: v for k, v in GITHUB_CLAIMS.items() if k != "sub"}
    with pytest.raises(tp.TrustedPublishingError, match="not valid"):
        tp.authorize(_make_token(claims), [GITHUB_PUBLISHER])


def test_issuer_the_feedstock_never_named_is_refused_without_fetching(monkeypatch):
    def _explode(*args, **kwargs):
        raise AssertionError("went looking for an issuer nobody named")

    monkeypatch.setattr(tp, "_fetch_json", _explode)
    monkeypatch.setattr(tp, "_signing_key", _explode)

    token = _make_token(GITHUB_CLAIMS, iss="https://issuer.example.invalid")
    with pytest.raises(tp.TrustedPublishingError, match="not an issuer this feedstock"):
        tp.authorize(token, [GITHUB_PUBLISHER])


def test_feedstock_with_no_publishers_is_refused(trusted_key):
    with pytest.raises(tp.TrustedPublishingError, match="no trusted publishers"):
        tp.authorize(_make_token(GITHUB_CLAIMS), [])


def test_self_hosted_gitlab_works_when_named(trusted_key):
    claims = dict(GITLAB_CLAIMS, iss="https://gitlab.cern.ch")
    publisher = GITLAB_PUBLISHER.model_copy(update={"url": "https://gitlab.cern.ch"})

    matched, _ = tp.authorize(_make_token(claims), [publisher])
    assert matched is publisher

    # a gitlab.com token must not satisfy a self-hosted publisher
    with pytest.raises(tp.TrustedPublishingError, match="not an issuer this feedstock"):
        tp.authorize(_make_token(GITLAB_CLAIMS), [publisher])

    # nor the other way round
    with pytest.raises(tp.TrustedPublishingError, match="not an issuer this feedstock"):
        tp.authorize(_make_token(claims), [GITLAB_PUBLISHER])


def test_gitlab_too_old_to_send_its_claims_is_refused(trusted_key):
    claims = {k: v for k, v in GITLAB_CLAIMS.items() if k != "ref_protected"}
    with pytest.raises(tp.TrustedPublishingError, match="does not carry ref_protected"):
        tp.authorize(_make_token(claims), [GITLAB_PUBLISHER])


def test_github_token_without_the_claims_we_match_on_is_refused(trusted_key):
    claims = {k: v for k, v in GITHUB_CLAIMS.items() if k != "job_workflow_ref"}
    with pytest.raises(
        tp.TrustedPublishingError, match="does not carry job_workflow_ref"
    ):
        tp.authorize(_make_token(claims), [GITHUB_PUBLISHER])


def test_a_workflow_called_by_the_named_one_does_not_authorize(trusted_key):
    """job_workflow_ref, not workflow_ref, is what names the job's own workflow.

    The run still starts at deploy.yml, so pinning where it started would let
    anything it calls publish in its name.
    """
    token = _make_token(
        dict(
            GITHUB_CLAIMS,
            job_workflow_ref="other/repo/.github/workflows/build.yml@refs/heads/main",
        )
    )
    with pytest.raises(tp.TrustedPublishingError, match="matches no trusted publisher"):
        tp.authorize(token, [GITHUB_PUBLISHER])


def test_an_old_provider_reads_differently_from_a_mismatch(trusted_key):
    """The two are easy to confuse when debugging, so they must not look alike."""
    old = {k: v for k, v in GITLAB_CLAIMS.items() if k != "ref_type"}
    with pytest.raises(tp.TrustedPublishingError) as too_old:
        tp.authorize(_make_token(old), [GITLAB_PUBLISHER])

    mismatched = dict(GITLAB_CLAIMS, project_path="somebody/else")
    with pytest.raises(tp.TrustedPublishingError) as no_match:
        tp.authorize(_make_token(mismatched), [GITLAB_PUBLISHER])

    assert "does not carry" in str(too_old.value)
    assert "matches no trusted publisher" in str(no_match.value)


@pytest.mark.parametrize(
    "claims",
    [
        {"repository": "DIRACGrid/DIRACX"},
        {
            "job_workflow_ref": (
                "DIRACGrid/DIRAC/.github/workflows/other.yml@refs/heads/main"
            )
        },
        # the same workflow name, but somewhere else
        {
            "repository": "DIRACGrid/DIRAC",
            "job_workflow_ref": (
                "evil/DIRAC/.github/workflows/deploy.yml@refs/heads/main"
            ),
        },
        # a file whose name merely starts the same way
        {
            "job_workflow_ref": (
                "DIRACGrid/DIRAC/.github/workflows/deploy.yml.bak@refs/heads/main"
            )
        },
    ],
)
def test_github_mismatches_do_not_authorize(trusted_key, claims):
    token = _make_token(dict(GITHUB_CLAIMS, **claims))
    with pytest.raises(tp.TrustedPublishingError, match="matches no trusted publisher"):
        tp.authorize(token, [GITHUB_PUBLISHER])


@pytest.mark.parametrize(
    "claims,publisher,name",
    [
        (GITHUB_CLAIMS, GITHUB_PUBLISHER, "repository_owner_id"),
        (GITLAB_CLAIMS, GITLAB_PUBLISHER, "namespace_id"),
    ],
)
def test_the_id_pin_catches_a_path_that_changed_hands(
    trusted_key, claims, publisher, name
):
    token = _make_token(dict(claims, **{name: "99999"}))
    with pytest.raises(tp.TrustedPublishingError, match="matches no trusted publisher"):
        tp.authorize(token, [publisher])


def test_github_environment_is_required_when_given(trusted_key):
    publisher = GITHUB_PUBLISHER.model_copy(update={"environment": "release"})

    with pytest.raises(tp.TrustedPublishingError, match="matches no trusted publisher"):
        tp.authorize(_make_token(GITHUB_CLAIMS), [publisher])

    token = _make_token(dict(GITHUB_CLAIMS, environment="release"))
    assert tp.authorize(token, [publisher])[0] is publisher


def test_gitlab_unprotected_ref_is_rejected_when_protection_required(trusted_key):
    token = _make_token(dict(GITLAB_CLAIMS, ref_protected="false"))
    with pytest.raises(tp.TrustedPublishingError, match="matches no trusted publisher"):
        tp.authorize(token, [GITLAB_PUBLISHER])


def test_gitlab_branch_is_rejected_when_a_tag_is_required(trusted_key):
    token = _make_token(dict(GITLAB_CLAIMS, ref_type="branch"))
    with pytest.raises(tp.TrustedPublishingError, match="matches no trusted publisher"):
        tp.authorize(token, [GITLAB_PUBLISHER])


def test_gitlab_booleans_may_be_real_booleans(trusted_key):
    token = _make_token(dict(GITLAB_CLAIMS, ref_protected=True))
    assert tp.authorize(token, [GITLAB_PUBLISHER])[0] is GITLAB_PUBLISHER


@pytest.fixture(autouse=True)
def _no_key_cache():
    tp._jwks.cache.clear()
    yield
    tp._jwks.cache.clear()


def test_discovery_document_must_claim_the_issuer(monkeypatch):
    issuer = "https://issuer-lies.invalid"
    monkeypatch.setattr(
        tp,
        "_fetch_json",
        lambda url: {"issuer": tp.GITHUB_ISSUER, "jwks_uri": f"{issuer}/keys"},
    )
    with pytest.raises(tp.TrustedPublishingError, match="discovery document belonging"):
        tp._jwks(issuer)


def test_keys_must_be_served_by_the_issuer(monkeypatch):
    issuer = "https://issuer-offsite-keys.invalid"
    monkeypatch.setattr(
        tp,
        "_fetch_json",
        lambda url: {
            "issuer": issuer,
            "jwks_uri": "https://somewhere-else.invalid/keys",
        },
    )
    with pytest.raises(tp.TrustedPublishingError, match="off its own host"):
        tp._jwks(issuer)


def _resolves_to(monkeypatch, *addresses):
    monkeypatch.setattr(
        tp.socket,
        "getaddrinfo",
        lambda host, port, **kw: [
            (tp.socket.AF_INET, tp.socket.SOCK_STREAM, 6, "", (addr, port))
            for addr in addresses
        ],
    )


class _FakeResponse:
    def __init__(self, body=b"{}", status_code=200):
        self.status_code = status_code
        self.headers = {}
        self.raw = self

    def read(self, amount, decode_content=False):
        return self._body[:amount]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _serves(monkeypatch, body=b"{}", status_code=200):
    def _get(url, **kwargs):
        assert kwargs["allow_redirects"] is False, "redirects must not be followed"
        assert kwargs["timeout"] == (tp.CONNECT_TIMEOUT, tp.READ_TIMEOUT)
        res = _FakeResponse(status_code=status_code)
        res._body = body
        return res

    monkeypatch.setattr(requests, "get", _get)


@pytest.mark.parametrize(
    "url,message",
    [
        ("http://example.invalid/x", "is not https"),
        ("ftp://example.invalid/x", "is not https"),
        ("https://user:pw@example.invalid/x", "carries credentials"),
        ("https:///x", "has no host"),
    ],
)
def test_only_plain_https_urls_are_fetched(url, message):
    with pytest.raises(tp.TrustedPublishingError, match=message):
        tp._fetch_json(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        # the address a cloud instance keeps its credentials on
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "::1",
        "fd00::1",
    ],
)
def test_addresses_off_the_public_internet_are_refused(monkeypatch, address):
    _resolves_to(monkeypatch, address)
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: pytest.fail("connected to a private address")
    )
    with pytest.raises(tp.TrustedPublishingError, match="not on the public internet"):
        tp._fetch_json("https://rebinding.invalid/x")


def test_one_bad_address_among_good_ones_is_enough(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34", "127.0.0.1")
    with pytest.raises(tp.TrustedPublishingError, match="not on the public internet"):
        tp._fetch_json("https://half-private.invalid/x")


def test_a_public_address_is_fetched(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    _serves(monkeypatch, body=b'{"hello": "world"}')
    assert tp._fetch_json("https://example.invalid/x") == {"hello": "world"}


def test_redirects_are_not_followed(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    _serves(monkeypatch, body=b"", status_code=302)
    with pytest.raises(tp.TrustedPublishingError, match="answered 302"):
        tp._fetch_json("https://redirector.invalid/x")


def test_an_oversized_response_is_refused(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    _serves(monkeypatch, body=b"x" * (tp.MAX_RESPONSE_BYTES + 10))
    with pytest.raises(tp.TrustedPublishingError, match="more than we will read"):
        tp._fetch_json("https://firehose.invalid/x")


def test_a_non_json_response_is_refused(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    _serves(monkeypatch, body=b"<html>nope</html>")
    with pytest.raises(tp.TrustedPublishingError, match="did not return json"):
        tp._fetch_json("https://not-json.invalid/x")


def test_describe_names_the_job():
    assert "deploy.yml" in tp.describe(GITHUB_CLAIMS)
    assert "35107909883" in tp.describe(GITHUB_CLAIMS)

    described = tp.describe(GITLAB_CLAIMS)
    assert "gitlab.com/lhcb-core/LbEnv" in described
    assert "tag v2.4.0" in described
