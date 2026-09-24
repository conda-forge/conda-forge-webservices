import asyncio
import base64
import concurrent.futures
import datetime
import hmac
import inspect
import ipaddress
import json
import logging
import socket
import ssl
import time
import uuid

import jwt
import pydantic
import pytest
import tornado.httpclient
from conda_smithy.schema import GitHubTrustedPublisher, GitLabTrustedPublisher
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from conda_forge_webservices import trusted_publishing as tp


@pytest.fixture(autouse=True)
def _nothing_held(monkeypatch):
    """Every test starts holding no keys, having spent no tokens and asked for
    no refreshes, and nothing is fetched unless it says so."""
    state = [tp._KEYS, tp._SPENT, tp._SERVED, tp._REFRESHING, tp._ASKED]
    for held in state:
        held.clear()
    monkeypatch.setattr(tp, "_refresh_soon", None)
    yield
    for held in state:
        held.clear()


@pytest.fixture
def asked(monkeypatch):
    """The issuers the request path asked to have refreshed, in order."""
    asked = []
    monkeypatch.setattr(tp, "_refresh_soon", asked.append)
    return asked


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

CERN = "https://gitlab.cern.ch"


def _make_token(
    claims,
    *,
    key=KEY,
    algorithm="RS256",
    lifetime_minutes=5,
    headers=None,
    **overrides,
):
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "aud": tp.AUDIENCE,
        "iat": now,
        "exp": now + datetime.timedelta(minutes=lifetime_minutes),
        "jti": uuid.uuid4().hex,
        **claims,
        **overrides,
    }
    return jwt.encode(payload, key, algorithm=algorithm, headers=headers)


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _hand_built(header, payload):
    """A token pyjwt would refuse to encode, so that we can send one anyway."""
    return b".".join(
        [
            _b64(json.dumps(header).encode()),
            _b64(json.dumps(payload).encode()),
            _b64(b"signature"),
        ]
    ).decode()


def _public_jwk(key, kid, use=None):
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = kid
    if use is not None:
        jwk["use"] = use
    return jwk


def _hold(issuer, *jwks):
    """Hold these keys for an issuer, as whatever fetches them would."""
    tp._KEYS[issuer] = jwt.PyJWKSet.from_dict({"keys": list(jwks)})


def _authorize(token, publishers):
    """Verify a token, then match it to the publishers, as a caller does."""
    claims = tp.verify_token(token)
    return tp.authorize(claims, publishers), claims


@pytest.fixture
def trusted_key(monkeypatch):
    """Take our own public key as the signing key for every issuer."""
    monkeypatch.setattr(tp, "_signing_key", lambda issuer, token: KEY.public_key())


def test_github_token_authorizes(trusted_key):
    publisher, claims = _authorize(_make_token(GITHUB_CLAIMS), [GITHUB_PUBLISHER])
    assert publisher is GITHUB_PUBLISHER
    assert claims["repository"] == "DIRACGrid/DIRAC"


def test_gitlab_token_authorizes(trusted_key):
    publisher, claims = _authorize(_make_token(GITLAB_CLAIMS), [GITLAB_PUBLISHER])
    assert publisher is GITLAB_PUBLISHER
    assert claims["project_path"] == "lhcb-core/LbEnv"


def test_first_matching_publisher_wins(trusted_key):
    publisher, _ = _authorize(
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


def test_a_provider_this_version_cannot_check_is_skipped(monkeypatch):
    """A schema that knows a provider before this module does is not fatal."""
    monkeypatch.setattr(tp, "MATCHERS", {GitHubTrustedPublisher: tp._matches_github})
    publishers = tp.parse_publishers(
        [GITLAB_PUBLISHER.model_dump(), GITHUB_PUBLISHER.model_dump()]
    )
    assert publishers == [GITHUB_PUBLISHER]


def test_every_shipped_issuer_serves_its_keys_from_its_own_host():
    for allowed in tp.ALLOWED_ISSUERS.values():
        assert allowed.jwks_uri.startswith(f"{allowed.issuer}/")


def test_the_shipped_allowlist_reads():
    assert tp.ALLOWED_ISSUERS[tp.GITHUB_ISSUER].provider == "github"
    assert tp.ALLOWED_ISSUERS["https://gitlab.com"].provider == "gitlab"
    assert tp.ALLOWED_ISSUERS[CERN].provider == "gitlab"


@pytest.mark.parametrize(
    "entries",
    [
        # listed twice
        [
            {
                "issuer": "https://gitlab.com",
                "provider": "gitlab",
                "jwks_uri": "https://gitlab.com/k",
            },
            {
                "issuer": "https://gitlab.com",
                "provider": "gitlab",
                "jwks_uri": "https://gitlab.com/k",
            },
        ],
        # iss is compared as written, and no issuer writes a trailing slash
        [
            {
                "issuer": "https://gitlab.com/",
                "provider": "gitlab",
                "jwks_uri": "https://gitlab.com/k",
            }
        ],
        [
            {
                "issuer": "http://gitlab.com",
                "provider": "gitlab",
                "jwks_uri": "http://gitlab.com/k",
            }
        ],
        [
            {
                "issuer": "https://gitlab.com",
                "provider": "bitbucket",
                "jwks_uri": "https://gitlab.com/k",
            }
        ],
        [
            {
                "issuer": "https://gitlab.com",
                "provider": "gitlab",
                "jwks_uri": "https://gitlab.com/k",
                "namespaces": [1],
            }
        ],
        # keys served from anywhere but the issuer's own host
        [
            {
                "issuer": "https://gitlab.com",
                "provider": "gitlab",
                "jwks_uri": "https://gitlab.com.evil.example/keys",
            }
        ],
        [
            {
                "issuer": "https://gitlab.com",
                "provider": "gitlab",
                "jwks_uri": "https://elsewhere.example/keys",
            }
        ],
        [{"issuer": "https://gitlab.com", "provider": "gitlab"}],
        [
            {
                "issuer": "https://gitlab.com",
                "provider": "gitlab",
                "jwks_uri": "https://gitlab.com/k eys",
            }
        ],
    ],
)
def test_an_allowlist_that_says_something_unclear_does_not_load(entries):
    with pytest.raises((ValueError, pydantic.ValidationError)):
        tp._load_allowed_issuers(json.dumps(entries))


@pytest.mark.parametrize(
    "entry",
    [
        # a GitLab instance nobody has listed
        dict(GITLAB_PUBLISHER.model_dump(), url="https://gitlab.example.org"),
        # a gitlab entry naming github's issuer, which would otherwise be judged
        # by the gitlab matcher against claims github never meant that way
        dict(GITLAB_PUBLISHER.model_dump(), url=tp.GITHUB_ISSUER),
    ],
)
def test_a_publisher_the_allowlist_does_not_cover_is_dropped(entry):
    assert tp.parse_publishers([entry, GITHUB_PUBLISHER.model_dump()]) == [
        GITHUB_PUBLISHER
    ]


def test_a_gitlab_url_with_a_trailing_slash_does_not_parse():
    """iss is compared as written, which conda-smithy's schema already ensures.

    If it ever stops doing so this starts failing, and the url needs
    normalising before it is compared.
    """
    entry = dict(GITLAB_PUBLISHER.model_dump(), url="https://gitlab.com/")
    assert tp.parse_publishers([entry]) == []


@pytest.mark.parametrize(
    "publisher",
    [
        GITLAB_PUBLISHER.model_copy(update={"url": "https://gitlab.example.org"}),
        # a gitlab entry naming github's issuer
        GITLAB_PUBLISHER.model_copy(update={"url": tp.GITHUB_ISSUER}),
    ],
)
def test_authorize_applies_the_allowlist_to_publishers_it_is_handed(
    trusted_key, caplog, publisher
):
    """authorize may be handed publishers that never went through parsing."""
    claims = dict(GITLAB_CLAIMS, iss=publisher.url, jti="j", iat=0, exp=0)
    with pytest.raises(tp.TrustedPublishingError, match="no trusted publishers"):
        tp.authorize(claims, [publisher])

    # what was wrong with it reaches the log, as parsing it would have said
    assert "ignoring a trusted publisher" in caplog.text


def test_no_entries_at_all_reads_as_none():
    assert tp.parse_publishers(None) == []


@pytest.mark.parametrize("entries", [5, True, "github", {"provider": "github"}])
def test_trusted_publishers_that_is_not_a_list_reads_as_none(entries):
    """conda-forge.yml is feedstock controlled, so the key can be anything."""
    assert tp.parse_publishers(entries) == []


def test_token_for_another_audience_is_rejected(trusted_key):
    token = _make_token(GITHUB_CLAIMS, aud="pypi")
    with pytest.raises(tp.TrustedPublishingError, match="audience is not"):
        _authorize(token, [GITHUB_PUBLISHER])


def test_expired_token_is_rejected(trusted_key):
    token = _make_token(GITHUB_CLAIMS, lifetime_minutes=-5)
    with pytest.raises(tp.TrustedPublishingError, match="has expired"):
        _authorize(token, [GITHUB_PUBLISHER])


def test_token_signed_by_another_key_is_rejected(trusted_key):
    token = _make_token(GITHUB_CLAIMS, key=OTHER_KEY)
    with pytest.raises(tp.TrustedPublishingError, match="not valid"):
        _authorize(token, [GITHUB_PUBLISHER])


def test_unsigned_token_is_rejected(trusted_key):
    token = _make_token(GITHUB_CLAIMS, key=None, algorithm="none")
    with pytest.raises(tp.TrustedPublishingError, match="not valid"):
        _authorize(token, [GITHUB_PUBLISHER])


def test_a_clock_a_little_ahead_still_authorizes(trusted_key):
    """A runner's clock and ours do not agree to the second."""
    ahead = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=10
    )
    token = _make_token(GITHUB_CLAIMS, iat=ahead)
    assert _authorize(token, [GITHUB_PUBLISHER])[0] is GITHUB_PUBLISHER


def test_token_missing_a_required_claim_is_rejected(trusted_key):
    claims = {k: v for k, v in GITHUB_CLAIMS.items() if k != "sub"}
    with pytest.raises(tp.TrustedPublishingError, match="not valid"):
        _authorize(_make_token(claims), [GITHUB_PUBLISHER])


def test_an_issuer_off_the_allowlist_is_refused_before_any_key(monkeypatch):
    def _explode(*args, **kwargs):
        raise AssertionError("looked for a key of an issuer nobody listed")

    monkeypatch.setattr(tp, "_signing_key", _explode)

    token = _make_token(GITHUB_CLAIMS, iss="https://issuer.example.invalid")
    with pytest.raises(tp.TrustedPublishingError, match="not one conda-forge accepts"):
        tp.verify_token(token)


def test_a_listed_issuer_the_feedstock_does_not_name_is_refused(trusted_key):
    token = _make_token(GITLAB_CLAIMS)
    with pytest.raises(
        tp.TrustedPublishingError, match="not one this feedstock publishes from"
    ):
        _authorize(token, [GITHUB_PUBLISHER])


def test_a_token_is_verified_without_knowing_the_feedstock(trusted_key):
    """So a caller can refuse one that is not genuine before reading anything."""
    claims = tp.verify_token(_make_token(GITHUB_CLAIMS))
    assert claims["repository"] == "DIRACGrid/DIRAC"
    # and verifying spends nothing; only a match does
    assert tp._SPENT == {}


def test_an_issuer_that_is_not_a_string_is_refused(trusted_key):
    """`iss` reaches a dict lookup, which an unhashable claim would blow up."""
    token = _hand_built({"alg": "RS256"}, dict(GITHUB_CLAIMS, iss=[]))
    with pytest.raises(tp.TrustedPublishingError, match="not one conda-forge accepts"):
        tp.verify_token(token)


def test_a_token_that_cannot_be_read_says_only_that(caplog):
    caplog.set_level(logging.INFO, logger=tp.LOGGER.name)
    with pytest.raises(tp.TrustedPublishingError) as refused:
        _authorize("not-a.jwt", [GITHUB_PUBLISHER])

    assert str(refused.value) == "the token could not be read"
    # pyjwt's own account of what was wrong is for us, in the log
    assert refused.value.detail
    assert refused.value.detail in caplog.text


def test_an_issuer_the_caller_wrote_is_not_repeated_back(caplog):
    caplog.set_level(logging.INFO, logger=tp.LOGGER.name)
    written = "https://attacker.example/<script>"
    token = _make_token(GITHUB_CLAIMS, iss=written)
    with pytest.raises(tp.TrustedPublishingError) as refused:
        _authorize(token, [GITHUB_PUBLISHER])

    assert written not in str(refused.value)
    assert written in caplog.text


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "pypi"},
        {"exp": 1},
        {"nbf": 4102444800},
    ],
)
def test_what_is_wrong_with_a_token_is_said_without_pyjwts_words(trusted_key, claims):
    with pytest.raises(tp.TrustedPublishingError) as refused:
        _authorize(_make_token(GITHUB_CLAIMS, **claims), [GITHUB_PUBLISHER])
    assert refused.value.detail not in str(refused.value)


def test_a_mismatch_does_not_repeat_what_the_sender_named(trusted_key, caplog):
    """Anyone can have GitHub sign a token for a branch named as they like."""
    caplog.set_level(logging.INFO, logger=tp.LOGGER.name)
    branch = "refs/heads/<script>alert(1)</script>"
    token = _make_token(
        dict(
            GITHUB_CLAIMS,
            job_workflow_ref=f"someone/else/.github/workflows/deploy.yml@{branch}",
        )
    )
    with pytest.raises(tp.TrustedPublishingError) as refused:
        _authorize(token, [GITHUB_PUBLISHER])

    assert str(refused.value) == "the token matches no trusted publisher"
    assert branch in refused.value.detail
    assert branch in caplog.text


def test_feedstock_with_no_publishers_is_refused(trusted_key):
    with pytest.raises(tp.TrustedPublishingError, match="no trusted publishers"):
        _authorize(_make_token(GITHUB_CLAIMS), [])


def test_self_hosted_gitlab_works_when_named(trusted_key):
    claims = dict(GITLAB_CLAIMS, iss="https://gitlab.cern.ch")
    publisher = GITLAB_PUBLISHER.model_copy(update={"url": "https://gitlab.cern.ch"})

    matched, _ = _authorize(_make_token(claims), [publisher])
    assert matched is publisher

    # a gitlab.com token must not satisfy a self-hosted publisher
    with pytest.raises(
        tp.TrustedPublishingError, match="not one this feedstock publishes from"
    ):
        _authorize(_make_token(GITLAB_CLAIMS), [publisher])

    # nor the other way round
    with pytest.raises(
        tp.TrustedPublishingError, match="not one this feedstock publishes from"
    ):
        _authorize(_make_token(claims), [GITLAB_PUBLISHER])


def test_gitlab_too_old_to_send_its_claims_is_refused(trusted_key):
    claims = {k: v for k, v in GITLAB_CLAIMS.items() if k != "ref_protected"}
    with pytest.raises(tp.TrustedPublishingError, match="does not carry ref_protected"):
        _authorize(_make_token(claims), [GITLAB_PUBLISHER])


def test_github_token_without_the_claims_we_match_on_is_refused(trusted_key):
    claims = {k: v for k, v in GITHUB_CLAIMS.items() if k != "job_workflow_ref"}
    with pytest.raises(
        tp.TrustedPublishingError, match="does not carry job_workflow_ref"
    ):
        _authorize(_make_token(claims), [GITHUB_PUBLISHER])


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
        _authorize(token, [GITHUB_PUBLISHER])


@pytest.mark.parametrize("shadow_first", [True, False])
def test_an_entry_for_another_provider_does_not_block_a_good_one(
    trusted_key, monkeypatch, shadow_first
):
    """A gitlab entry naming github's issuer does not refuse a github token.

    The allowlist drops such an entry now, so it is let through here to check
    the claim floor on its own: a github token carries none of the claims that
    entry wants, which is a fact about the entry rather than about the token.
    """
    monkeypatch.setattr(tp, "_not_allowed", lambda publisher: None)
    shadow = GitLabTrustedPublisher(
        provider="gitlab",
        url=tp.GITHUB_ISSUER,
        project_path="somebody/else",
        namespace_id=9999,
    )
    publishers = (
        [shadow, GITHUB_PUBLISHER] if shadow_first else [GITHUB_PUBLISHER, shadow]
    )

    matched, _ = _authorize(_make_token(GITHUB_CLAIMS), publishers)
    assert matched is GITHUB_PUBLISHER


def test_an_old_provider_reads_differently_from_a_mismatch(trusted_key):
    """The two are easy to confuse when debugging, so they must not look alike."""
    old = {k: v for k, v in GITLAB_CLAIMS.items() if k != "ref_type"}
    with pytest.raises(tp.TrustedPublishingError) as too_old:
        _authorize(_make_token(old), [GITLAB_PUBLISHER])

    mismatched = dict(GITLAB_CLAIMS, project_path="somebody/else")
    with pytest.raises(tp.TrustedPublishingError) as no_match:
        _authorize(_make_token(mismatched), [GITLAB_PUBLISHER])

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
        _authorize(token, [GITHUB_PUBLISHER])


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
        _authorize(token, [publisher])


@pytest.mark.parametrize(
    "claims,publisher",
    [
        (
            dict(
                GITHUB_CLAIMS,
                repository="diracgrid/dirac",
                repository_owner="diracgrid",
                job_workflow_ref=(
                    "diracgrid/dirac/.github/workflows/deploy.yml@refs/tags/v9.1.19"
                ),
            ),
            GITHUB_PUBLISHER,
        ),
        (
            dict(GITHUB_CLAIMS, environment="Release"),
            GITHUB_PUBLISHER.model_copy(update={"environment": "release"}),
        ),
        (dict(GITLAB_CLAIMS, project_path="LHCb-Core/lbenv"), GITLAB_PUBLISHER),
    ],
)
def test_names_are_compared_without_regard_to_case(trusted_key, claims, publisher):
    assert _authorize(_make_token(claims), [publisher])[0] is publisher


@pytest.mark.parametrize(
    "claims,publisher",
    [
        # the workflow is a file in git, where case is part of the name
        (
            dict(
                GITHUB_CLAIMS,
                job_workflow_ref=(
                    "DIRACGrid/DIRAC/.github/workflows/Deploy.yml@refs/tags/v9.1.19"
                ),
            ),
            GITHUB_PUBLISHER,
        ),
        (
            dict(GITLAB_CLAIMS, environment="Release"),
            GITLAB_PUBLISHER.model_copy(update={"environment": "release"}),
        ),
    ],
)
def test_what_is_not_a_name_keeps_its_case(trusted_key, claims, publisher):
    with pytest.raises(tp.TrustedPublishingError, match="matches no trusted publisher"):
        _authorize(_make_token(claims), [publisher])


@pytest.mark.parametrize("claim", ["repository", "job_workflow_ref", "environment"])
def test_a_name_that_is_not_a_string_does_not_match(trusted_key, claim):
    publisher = GITHUB_PUBLISHER.model_copy(update={"environment": "release"})
    claims = {**GITHUB_CLAIMS, "environment": "release", claim: ["DIRACGrid/DIRAC"]}
    with pytest.raises(tp.TrustedPublishingError, match="matches no trusted publisher"):
        _authorize(_make_token(claims), [publisher])


def test_github_environment_is_required_when_given(trusted_key):
    publisher = GITHUB_PUBLISHER.model_copy(update={"environment": "release"})

    with pytest.raises(tp.TrustedPublishingError, match="matches no trusted publisher"):
        _authorize(_make_token(GITHUB_CLAIMS), [publisher])

    token = _make_token(dict(GITHUB_CLAIMS, environment="release"))
    assert _authorize(token, [publisher])[0] is publisher


def test_gitlab_unprotected_ref_is_rejected_when_protection_required(trusted_key):
    token = _make_token(dict(GITLAB_CLAIMS, ref_protected="false"))
    with pytest.raises(tp.TrustedPublishingError, match="matches no trusted publisher"):
        _authorize(token, [GITLAB_PUBLISHER])


def test_gitlab_branch_is_rejected_when_a_tag_is_required(trusted_key):
    token = _make_token(dict(GITLAB_CLAIMS, ref_type="branch"))
    with pytest.raises(tp.TrustedPublishingError, match="matches no trusted publisher"):
        _authorize(token, [GITLAB_PUBLISHER])


def test_gitlab_booleans_may_be_real_booleans(trusted_key):
    token = _make_token(dict(GITLAB_CLAIMS, ref_protected=True))
    assert _authorize(token, [GITLAB_PUBLISHER])[0] is GITLAB_PUBLISHER


def test_a_token_verifies_against_the_keys_held(caplog):
    """The whole path, with nothing patched: PyJWK, signature, claims."""
    _hold(tp.GITHUB_ISSUER, _public_jwk(KEY, "k1"))
    token = _make_token(GITHUB_CLAIMS, headers={"kid": "k1"})

    matched, claims = _authorize(token, [GITHUB_PUBLISHER])
    assert matched is GITHUB_PUBLISHER
    assert claims["repository"] == "DIRACGrid/DIRAC"


def test_a_signature_from_another_key_fails_against_the_held_ones():
    _hold(tp.GITHUB_ISSUER, _public_jwk(KEY, "k1"))
    token = _make_token(GITHUB_CLAIMS, key=OTHER_KEY, headers={"kid": "k1"})

    with pytest.raises(tp.TrustedPublishingError, match="not valid"):
        _authorize(token, [GITHUB_PUBLISHER])


def test_an_issuer_whose_keys_are_not_held_yet_is_retryable(asked):
    token = _make_token(GITHUB_CLAIMS, headers={"kid": "k1"})
    with pytest.raises(tp.TrustedPublishingError, match="not loaded yet") as refused:
        _authorize(token, [GITHUB_PUBLISHER])
    assert refused.value.retryable
    assert asked == [tp.GITHUB_ISSUER]


def test_a_key_the_issuer_does_not_publish_is_retryable(asked):
    """Usually the issuer has rotated since its keys were last fetched."""
    _hold(tp.GITHUB_ISSUER, _public_jwk(KEY, "old"))
    token = _make_token(GITHUB_CLAIMS, headers={"kid": "new"})
    with pytest.raises(tp.TrustedPublishingError, match="does not publish") as refused:
        _authorize(token, [GITHUB_PUBLISHER])
    assert refused.value.retryable
    assert asked == [tp.GITHUB_ISSUER]


def test_a_token_that_names_no_key_is_refused(asked):
    _hold(tp.GITHUB_ISSUER, _public_jwk(KEY, "k1"))
    with pytest.raises(tp.TrustedPublishingError, match="does not name a signing key"):
        _authorize(_make_token(GITHUB_CLAIMS), [GITHUB_PUBLISHER])
    # or it would use up the issuer's early refresh
    assert asked == []


def test_a_kid_that_is_not_a_string_is_refused():
    _hold(tp.GITHUB_ISSUER, _public_jwk(KEY, "k1"))
    token = _hand_built({"alg": "RS256", "kid": 5}, dict(GITHUB_CLAIMS))
    # newer pyjwt refuses the header itself, before we look at the kid
    with pytest.raises(tp.TrustedPublishingError):
        _authorize(token, [GITHUB_PUBLISHER])


def test_a_key_published_for_encryption_does_not_verify():
    _hold(tp.GITHUB_ISSUER, _public_jwk(KEY, "k1", use="enc"))
    token = _make_token(GITHUB_CLAIMS, headers={"kid": "k1"})
    with pytest.raises(tp.TrustedPublishingError, match="does not publish the key"):
        _authorize(token, [GITHUB_PUBLISHER])


def test_a_symmetric_key_in_the_key_set_cannot_sign_a_token():
    """An issuer's oct key must not verify an HMAC made to look like RS256.

    pyjwt 2.10.1 checks a PyJWK's signature with the key's own algorithm
    whatever `algorithms` allows, so this token verifies there without the
    filter in _named_key.
    """
    secret = b"a secret the issuer's key set gives away"
    _hold(tp.GITHUB_ISSUER, {"kty": "oct", "kid": "k1", "k": _b64(secret).decode()})
    header = _b64(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    payload = _b64(
        json.dumps(
            dict(GITHUB_CLAIMS, aud=tp.AUDIENCE, iat=now, exp=now + 300, jti="j")
        ).encode()
    )
    signature = _b64(hmac.new(secret, header + b"." + payload, "sha256").digest())
    token = b".".join([header, payload, signature]).decode()

    with pytest.raises(tp.TrustedPublishingError, match="does not publish the key"):
        _authorize(token, [GITHUB_PUBLISHER])


def test_a_token_is_accepted_once(trusted_key):
    token = _make_token(GITHUB_CLAIMS)
    assert _authorize(token, [GITHUB_PUBLISHER])[0] is GITHUB_PUBLISHER

    with pytest.raises(tp.TrustedPublishingError, match="already been used"):
        _authorize(token, [GITHUB_PUBLISHER])


def test_a_spent_token_is_refused_by_verification_too(trusted_key):
    """So that a used token costs the caller nothing past verify_token."""
    token = _make_token(GITHUB_CLAIMS)
    _authorize(token, [GITHUB_PUBLISHER])

    with pytest.raises(tp.TrustedPublishingError, match="already been used"):
        tp.verify_token(token)


def test_another_token_from_the_same_job_is_accepted(trusted_key):
    _authorize(_make_token(GITHUB_CLAIMS), [GITHUB_PUBLISHER])
    _authorize(_make_token(GITHUB_CLAIMS), [GITHUB_PUBLISHER])


def test_the_same_jti_from_another_issuer_is_another_token(trusted_key):
    _authorize(_make_token(GITHUB_CLAIMS, jti="same"), [GITHUB_PUBLISHER])
    _authorize(_make_token(GITLAB_CLAIMS, jti="same"), [GITLAB_PUBLISHER])


def test_a_token_that_matched_nothing_is_not_spent(trusted_key):
    token = _make_token(GITHUB_CLAIMS)
    other = GITHUB_PUBLISHER.model_copy(update={"workflow": "other.yml"})
    with pytest.raises(tp.TrustedPublishingError, match="matches no trusted"):
        _authorize(token, [other])

    assert _authorize(token, [GITHUB_PUBLISHER])[0] is GITHUB_PUBLISHER


def test_a_token_refused_as_retryable_is_not_spent():
    token = _make_token(GITHUB_CLAIMS, headers={"kid": "k1"})
    with pytest.raises(tp.TrustedPublishingError, match="not loaded yet"):
        _authorize(token, [GITHUB_PUBLISHER])

    _hold(tp.GITHUB_ISSUER, _public_jwk(KEY, "k1"))
    assert _authorize(token, [GITHUB_PUBLISHER])[0] is GITHUB_PUBLISHER


@pytest.mark.parametrize("jti", [None, "", 5])
def test_a_token_without_a_usable_jti_is_refused(trusted_key, jti):
    claims = {k: v for k, v in GITHUB_CLAIMS.items()}
    token = _make_token(claims, jti=jti)
    with pytest.raises(tp.TrustedPublishingError, match="not valid"):
        _authorize(token, [GITHUB_PUBLISHER])


def _spend_another(now):
    """Spend a token at `now`, which is when the record is pruned."""
    tp._spend({"iss": tp.GITHUB_ISSUER, "jti": "another", "iat": now, "exp": now + 60})


def test_spent_tokens_are_forgotten_once_they_expire(trusted_key, monkeypatch):
    _authorize(_make_token(GITHUB_CLAIMS), [GITHUB_PUBLISHER])
    (expires,) = tp._SPENT.values()

    monkeypatch.setattr(tp, "_wall", lambda: expires + tp.LEEWAY + 1)
    _spend_another(expires + tp.LEEWAY + 1)
    assert list(tp._SPENT) == [(tp.GITHUB_ISSUER, "another")]


def _issued(minutes_ago, lasting_minutes):
    now = datetime.datetime.now(datetime.timezone.utc)
    issued = now - datetime.timedelta(minutes=minutes_ago)
    return {"iat": issued, "exp": issued + datetime.timedelta(minutes=lasting_minutes)}


def test_a_long_lived_token_is_taken_early_in_its_life(trusted_key):
    """GitLab's last as long as the job's timeout, an hour by default."""
    token = _make_token(GITLAB_CLAIMS, **_issued(minutes_ago=14, lasting_minutes=60))
    assert _authorize(token, [GITLAB_PUBLISHER])[0] is GITLAB_PUBLISHER


def test_a_token_issued_too_long_ago_is_expired_whatever_its_exp(trusted_key):
    token = _make_token(GITLAB_CLAIMS, **_issued(minutes_ago=16, lasting_minutes=60))
    with pytest.raises(tp.TrustedPublishingError) as refused:
        _authorize(token, [GITLAB_PUBLISHER])

    assert "issued more than 15 minutes ago" in str(refused.value)
    assert "sooner" in str(refused.value)
    assert not refused.value.retryable


def test_a_spent_long_lived_token_is_forgotten_once_too_old(trusted_key, monkeypatch):
    token = _make_token(GITLAB_CLAIMS, **_issued(minutes_ago=0, lasting_minutes=60))
    _, claims = _authorize(token, [GITLAB_PUBLISHER])

    too_old = claims["iat"] + tp.MAX_TOKEN_AGE + tp.LEEWAY + 1
    assert too_old < claims["exp"]
    monkeypatch.setattr(tp, "_wall", lambda: too_old)
    _spend_another(too_old)
    assert list(tp._SPENT) == [(tp.GITHUB_ISSUER, "another")]


def test_one_token_sent_at_once_is_accepted_once(trusted_key):
    token = _make_token(GITHUB_CLAIMS)

    def _try(_):
        try:
            _authorize(token, [GITHUB_PUBLISHER])
            return True
        except tp.TrustedPublishingError:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(_try, range(32))) == 1


def _self_signed(name):
    """A certificate for `name` that is its own CA, and its key, as PEM files."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


class _LocalIssuer:
    """A real https server on loopback, reached as issuer.test.

    Only the lookup is faked, so that issuer.test answers with the server's
    address; everything past it, from the vetting of that address to tls and
    http, is the code that runs in production. `respond` is called with each
    request's head and a writer to answer on.
    """

    HOST = "issuer.test"

    def __init__(self, tmp_path, monkeypatch):
        cert, key = _self_signed(self.HOST)
        (tmp_path / "cert.pem").write_bytes(cert)
        (tmp_path / "key.pem").write_bytes(key)

        self.server_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.server_context.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
        self.server_names = []
        self.server_context.sni_callback = lambda sock, name, ctx: (
            self.server_names.append(name)
        )

        self.requests = []
        self.lookups = []
        self.port = None
        self.respond = self._json(b"{}")

        monkeypatch.setattr(
            tp,
            "_ssl_context",
            lambda: ssl.create_default_context(cafile=tmp_path / "cert.pem"),
        )
        monkeypatch.setattr(tp, "_getaddrinfo", self._getaddrinfo)
        loopback = ipaddress.ip_address("127.0.0.1")
        monkeypatch.setattr(tp, "_is_public", lambda address: address == loopback)

    def _getaddrinfo(self, host, port, *args):
        self.lookups.append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", self.port))]

    @staticmethod
    def _json(body, head=b""):
        def _respond(request, writer):
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + head
                + b"Content-Length: %d\r\n\r\n" % len(body)
                + body
            )

        return _respond

    async def _serve(self, reader, writer):
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = await reader.read(4096)
                if not chunk:
                    return
                head += chunk
            self.requests.append(head)
            responded = self.respond(head, writer)
            if inspect.isawaitable(responded):
                await responded
            await writer.drain()
        except (ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()

    def fetch(self, *paths, host=HOST):
        """Fetch each path in turn, returning what the last one gave."""

        async def _run():
            server = await asyncio.start_server(
                self._serve, "127.0.0.1", 0, ssl=self.server_context
            )
            self.port = server.sockets[0].getsockname()[1]
            try:
                for path in paths or ["/x"]:
                    result = await tp._fetch_json(f"https://{host}{path}")
                return result
            finally:
                server.close()

        return asyncio.run(_run())


@pytest.fixture
def local_issuer(tmp_path, monkeypatch):
    return _LocalIssuer(tmp_path, monkeypatch)


@pytest.fixture
def short_timeouts(monkeypatch):
    monkeypatch.setattr(tp, "CONNECT_TIMEOUT", 0.5)
    monkeypatch.setattr(tp, "REQUEST_TIMEOUT", 0.5)


def test_keys_are_fetched_by_the_name_from_the_address_vetted(local_issuer):
    local_issuer.respond = local_issuer._json(b'{"keys": []}')
    assert local_issuer.fetch() == {"keys": []}
    # looked up once, and the certificate asked for and checked by name while
    # the connection went to the address the lookup gave
    assert local_issuer.lookups == [("issuer.test", 443)]
    assert local_issuer.server_names == ["issuer.test"]


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.0.0.1"],
        # where a cloud instance keeps its credentials
        ["169.254.169.254"],
        ["::1"],
        # one bad address among good ones is enough
        ["93.184.216.34", "127.0.0.1"],
    ],
)
def test_a_host_off_the_public_internet_is_not_connected_to(monkeypatch, addresses):
    monkeypatch.setattr(
        tp,
        "_getaddrinfo",
        lambda host, port, *args: [
            (
                socket.AF_INET6 if ":" in addr else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (addr, port),
            )
            for addr in addresses
        ],
    )
    with pytest.raises(ValueError, match="not public"):
        asyncio.run(tp._fetch_json("https://rebinding.invalid/keys"))


def test_a_certificate_for_another_name_is_refused(local_issuer):
    with pytest.raises(ssl.SSLCertVerificationError):
        local_issuer.fetch(host="other.test")


def test_redirects_are_not_followed(local_issuer):
    def _redirect(request, writer):
        writer.write(
            b"HTTP/1.1 302 Found\r\nLocation: https://issuer.test/y\r\n"
            b"Content-Length: 0\r\n\r\n"
        )

    local_issuer.respond = _redirect
    with pytest.raises(tornado.httpclient.HTTPClientError, match="302"):
        local_issuer.fetch()
    assert len(local_issuer.requests) == 1


def test_a_body_past_the_cap_is_refused(local_issuer, monkeypatch):
    body = json.dumps({"keys": ["x" * 1000]}).encode()
    local_issuer.respond = local_issuer._json(body)

    monkeypatch.setattr(tp, "MAX_RESPONSE_BYTES", len(body))
    assert local_issuer.fetch() == json.loads(body)

    monkeypatch.setattr(tp, "MAX_RESPONSE_BYTES", len(body) - 1)
    with pytest.raises(tornado.httpclient.HTTPClientError):
        local_issuer.fetch()


def test_a_server_that_drips_is_cut_off_at_the_deadline(local_issuer, short_timeouts):
    """Socket timeouts are per read, so slow enough bytes never trip them."""

    async def _drip(request, writer):
        writer.write(b"HTTP/1.1 200 OK\r\n")
        for _ in range(100):
            writer.write(b"x")
            await writer.drain()
            await asyncio.sleep(0.05)

    local_issuer.respond = _drip
    started = time.monotonic()
    with pytest.raises(tornado.httpclient.HTTPClientError, match="Timeout"):
        local_issuer.fetch()
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("body", [b"<html>nope</html>", b"[" * 200_000])
def test_a_body_that_is_not_json_is_refused(local_issuer, body):
    local_issuer.respond = local_issuer._json(body)
    with pytest.raises((ValueError, RecursionError)):
        local_issuer.fetch()


_AS_USUAL = object()


class _ServedIssuer:
    """One issuer's key set, counting what was fetched.

    Set `jwks` to serve something else, or `failure` to have the fetch fail.
    """

    def __init__(self, issuer, keys):
        self.issuer = issuer
        self.keys = keys
        self.jwks = _AS_USUAL
        self.failure = None
        self.fetches = []

    async def serve(self):
        self.fetches.append(tp.ALLOWED_ISSUERS[self.issuer].jwks_uri)
        if self.failure is not None:
            raise self.failure
        return {"keys": self.keys} if self.jwks is _AS_USUAL else self.jwks


@pytest.fixture
def issuers(monkeypatch):
    """Register issuers whose keys really are fetched, parsed and held."""
    registered = {}

    async def _fetch(url):
        for issuer, served in registered.items():
            if url == tp.ALLOWED_ISSUERS[issuer].jwks_uri:
                return await served.serve()
        raise AssertionError(f"fetched a key set nobody registered: {url}")

    monkeypatch.setattr(tp, "_fetch_json", _fetch)

    def _register(issuer, keys):
        registered[issuer] = _ServedIssuer(issuer, keys)
        return registered[issuer]

    return _register


def _refresh(issuer):
    """Run one background refresh to completion."""
    return asyncio.run(tp.refresh_keys(issuer))


def test_fetched_keys_verify_tokens_without_fetching_again(issuers):
    github = issuers(tp.GITHUB_ISSUER, [_public_jwk(KEY, "k1")])
    assert _refresh(tp.GITHUB_ISSUER)

    for _ in range(3):
        token = _make_token(GITHUB_CLAIMS, headers={"kid": "k1"})
        assert _authorize(token, [GITHUB_PUBLISHER])[0] is GITHUB_PUBLISHER
    assert github.fetches == [tp.ALLOWED_ISSUERS[tp.GITHUB_ISSUER].jwks_uri]


def test_the_request_path_never_fetches(issuers, asked):
    github = issuers(tp.GITHUB_ISSUER, [_public_jwk(KEY, "k1")])
    _refresh(tp.GITHUB_ISSUER)

    github.keys = [_public_jwk(KEY, "new")]
    token = _make_token(GITHUB_CLAIMS, headers={"kid": "new"})
    with pytest.raises(tp.TrustedPublishingError, match="does not publish"):
        _authorize(token, [GITHUB_PUBLISHER])
    assert len(github.fetches) == 1
    assert asked == [tp.GITHUB_ISSUER]

    # which is what the IOLoop then does with the ask
    _refresh(tp.GITHUB_ISSUER)
    assert _authorize(token, [GITHUB_PUBLISHER])[0] is GITHUB_PUBLISHER


def test_made_up_kids_ask_once_an_interval(asked, monkeypatch):
    _hold(tp.GITHUB_ISSUER, _public_jwk(KEY, "k1"))
    _hold(GITLAB_PUBLISHER.url, _public_jwk(KEY, "k1"))
    clock = [1000.0]
    monkeypatch.setattr(tp, "_now", lambda: clock[0])

    for attempt in range(5):
        token = _make_token(GITHUB_CLAIMS, headers={"kid": f"made-up-{attempt}"})
        with pytest.raises(tp.TrustedPublishingError, match="does not publish"):
            _authorize(token, [GITHUB_PUBLISHER])
    token = _make_token(GITLAB_CLAIMS, headers={"kid": "made-up"})
    with pytest.raises(tp.TrustedPublishingError, match="does not publish"):
        _authorize(token, [GITLAB_PUBLISHER])
    # once for each issuer, since one's asks do not use up another's
    assert asked == [tp.GITHUB_ISSUER, GITLAB_PUBLISHER.url]

    clock[0] += tp.KEY_REFRESH_INTERVAL
    token = _make_token(GITHUB_CLAIMS, headers={"kid": "made-up"})
    with pytest.raises(tp.TrustedPublishingError, match="does not publish"):
        _authorize(token, [GITHUB_PUBLISHER])
    assert asked[-1] == tp.GITHUB_ISSUER and len(asked) == 3


@pytest.mark.parametrize(
    "failure",
    [
        OSError("the issuer is down"),
        # nothing a refresh raises may escape, since nobody awaits it
        RuntimeError("something nobody thought of"),
    ],
)
def test_a_failed_refresh_keeps_the_last_good_keys(issuers, caplog, failure):
    github = issuers(tp.GITHUB_ISSUER, [_public_jwk(KEY, "k1")])
    gitlab = issuers(GITLAB_PUBLISHER.url, [_public_jwk(KEY, "k1")])
    assert _refresh(tp.GITHUB_ISSUER)
    assert _refresh(GITLAB_PUBLISHER.url)

    github.failure = failure
    assert not _refresh(tp.GITHUB_ISSUER)
    assert str(failure) in caplog.text

    for claims, publisher in [
        (GITHUB_CLAIMS, GITHUB_PUBLISHER),
        (GITLAB_CLAIMS, GITLAB_PUBLISHER),
    ]:
        token = _make_token(claims, headers={"kid": "k1"})
        assert _authorize(token, [publisher])[0] is publisher
    assert len(gitlab.fetches) == 1


RSA_JWK = _public_jwk(KEY, "k1")
EC_JWK = dict(
    json.loads(
        jwt.algorithms.ECAlgorithm.to_jwk(
            ec.generate_private_key(ec.SECP256R1()).public_key()
        )
    ),
    kid="k1",
)


@pytest.mark.parametrize(
    "jwks",
    [
        [],
        "x",
        None,
        {},
        {"keys": None},
        {"keys": {}},
        {"keys": ["x"]},
        {"keys": []},
        {"keys": [{"kty": 5}]},
        {"keys": [{"kty": "oct"}]},
        # keys pyjwt can use, but none that signs RS256
        {"keys": [{"kty": "oct", "kid": "k1", "k": "c2VjcmV0"}]},
        {"keys": [dict(RSA_JWK, use="enc")]},
        {"keys": [EC_JWK]},
        # pyjwt raises TypeError for these, not a PyJWTError
        {"keys": [dict(RSA_JWK, n=5)]},
        {"keys": [dict(RSA_JWK, e=None)]},
    ],
)
def test_a_key_set_of_the_wrong_shape_keeps_the_last_keys(issuers, jwks):
    served = issuers(GITLAB_PUBLISHER.url, [RSA_JWK])
    assert _refresh(served.issuer)

    served.jwks = jwks
    assert not _refresh(served.issuer)
    assert tp._named_key(tp._KEYS[served.issuer], "k1") is not None


def test_an_issuer_that_hangs_holds_up_no_other(issuers):
    """Nor a request: tokens from other issuers verify while it hangs."""
    github = issuers(tp.GITHUB_ISSUER, [_public_jwk(KEY, "gh")])
    issuers(GITLAB_PUBLISHER.url, [_public_jwk(KEY, "gl")])
    issuers(CERN, [_public_jwk(KEY, "cern")])

    async def _run():
        release = asyncio.Event()
        serve = github.serve

        async def _hang():
            await release.wait()
            return await serve()

        github.serve = _hang
        tasks = tp.refresh_all_keys()

        for _ in range(100):
            if GITLAB_PUBLISHER.url in tp._KEYS:
                break
            await asyncio.sleep(0.01)

        token = _make_token(GITLAB_CLAIMS, headers={"kid": "gl"})
        matched, _ = await asyncio.to_thread(_authorize, token, [GITLAB_PUBLISHER])
        assert matched is GITLAB_PUBLISHER

        # a second refresh of the hung issuer does not pile up behind it
        assert not await tp.refresh_keys(tp.GITHUB_ISSUER)

        release.set()
        assert all(await asyncio.gather(*tasks))

    asyncio.run(_run())
    assert tp.GITHUB_ISSUER in tp._KEYS


def test_an_issuer_off_the_allowlist_is_never_refreshed():
    assert not _refresh("https://gitlab.example.org")


def test_every_issuer_is_refreshed_at_startup_and_on_asking(monkeypatch):
    refreshed = []

    async def _refresh_keys(issuer):
        await asyncio.sleep(0)
        refreshed.append(issuer)
        return True

    monkeypatch.setattr(tp, "refresh_keys", _refresh_keys)

    async def _start():
        periodic = tp.start_key_refresh()
        try:
            # asked from a thread, as a request would
            await asyncio.to_thread(tp._ask_for_refresh, tp.GITHUB_ISSUER)
            for _ in range(10):
                await asyncio.sleep(0.01)
        finally:
            periodic.stop()

    asyncio.run(_start())
    assert sorted(refreshed) == sorted([*tp.ALLOWED_ISSUERS, tp.GITHUB_ISSUER])


def test_an_issuer_changing_its_keys_is_logged(issuers, caplog):
    served = issuers(CERN, [_public_jwk(KEY, "k1"), _public_jwk(OTHER_KEY, "k2")])
    _refresh(CERN)
    # neither the first keys, nor the same ones in another order, are a change
    served.keys = list(reversed(served.keys))
    _refresh(CERN)
    assert "now serves keys" not in caplog.text

    served.keys = [_public_jwk(KEY, "k3")]
    _refresh(CERN)
    assert "now serves keys ['k3'], where it served ['k1', 'k2']" in caplog.text


def test_describe_names_the_job():
    assert "deploy.yml" in tp.describe(GITHUB_CLAIMS)
    assert "35107909883" in tp.describe(GITHUB_CLAIMS)

    described = tp.describe(GITLAB_CLAIMS)
    assert "gitlab.com/lhcb-core/LbEnv" in described
    assert "tag v2.4.0" in described
