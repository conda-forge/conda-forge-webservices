"""Verify the identity tokens that CI providers issue to their own jobs.

A feedstock lists the jobs allowed to publish it in its conda-forge.yml, the job
sends the token its provider issued it, and this checks the token came from that
provider and matches an entry. Neither side holds a credential.

Anyone can send a token and any feedstock maintainer can edit its entries, so
an entry only chooses among the issuers in trusted_issuers.yaml, nothing is
read from a token before its signature is checked except which held key to
try, and each token is accepted once.
"""

import importlib.resources
import logging
import threading
import time
from typing import Any, Literal

import jwt
import pydantic
import yaml
from conda_smithy.schema import (
    GitHubTrustedPublisher,
    GitLabTrustedPublisher,
    TrustedPublisher,
)

LOGGER = logging.getLogger("conda_forge_webservices.trusted_publishing")

# Required in `aud`, so a token minted for PyPI cannot be replayed here.
AUDIENCE = "conda-forge-updater"

GITHUB_ISSUER = "https://token.actions.githubusercontent.com"

# Passing this explicitly rejects a token claiming "none", and a symmetric
# algorithm where the public key would be taken as the shared secret.
ALGORITHMS = ["RS256"]

REQUIRED_CLAIMS = ["iss", "aud", "exp", "iat", "sub", "jti"]

# a runner's clock and ours will not agree to the second
LEEWAY = 30

# How long after it was issued a token is taken, whatever its exp says.
# GitLab's last as long as the job's timeout, an hour by default, and one
# that leaks before it is used would otherwise be good for all of that.
MAX_TOKEN_AGE = 15 * 60

# the entries are written in conda-forge.yml, so conda-smithy's schema is both
# what describes them to a maintainer and what validates them here
_PUBLISHER = pydantic.TypeAdapter(TrustedPublisher)

# patched in tests
_wall = time.time


class AllowedIssuer(pydantic.BaseModel):
    """An issuer conda-forge accepts tokens from, as trusted_issuers.yaml lists it."""

    model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

    issuer: str = pydantic.Field(pattern=r"^https://[A-Za-z0-9.-]+$")
    provider: Literal["github", "gitlab"]


def _load_allowed_issuers(text: str) -> dict[str, AllowedIssuer]:
    entries = pydantic.TypeAdapter(list[AllowedIssuer]).validate_python(
        yaml.safe_load(text)
    )
    allowed = {entry.issuer: entry for entry in entries}
    if len(allowed) != len(entries):
        raise ValueError("an issuer is listed more than once")
    return allowed


# A broken edit to the list stops the webapp starting, rather than quietly
# accepting some other set of issuers.
ALLOWED_ISSUERS = _load_allowed_issuers(
    importlib.resources.files("conda_forge_webservices")
    .joinpath("trusted_issuers.yaml")
    .read_text()
)


class TrustedPublishingError(Exception):
    """A token could not be verified or matched no trusted publisher.

    str() of one is safe to show the caller; `detail` is for the log.
    `retryable` means the same request may succeed shortly.
    """

    def __init__(
        self, message: str, *, detail: str | None = None, retryable: bool = False
    ):
        super().__init__(message)
        self.detail = detail
        self.retryable = retryable


# Each issuer's signing keys. Checking a token only ever reads these; they are
# fetched elsewhere, never on a request.
_KEY_LOCK = threading.Lock()
_KEYS: dict[str, jwt.PyJWKSet] = {}


def _signs_rs256(key: jwt.PyJWK) -> bool:
    # a key an issuer publishes to be encrypted to is not one it signs with,
    # and pyjwt 2.10.1 verifies a PyJWK with the key's own algorithm, so an
    # oct key would let anyone who knows it HMAC-sign an "RS256" token
    return key.public_key_use in (None, "sig") and key.algorithm_name in ALGORITHMS


def _named_key(keys: jwt.PyJWKSet, kid: str) -> jwt.PyJWK | None:
    for key in keys.keys:
        if _signs_rs256(key) and key.key_id == kid:
            return key
    return None


def _signing_key(issuer: str, token: str) -> jwt.PyJWK:
    """The key an issuer says it signed a token with, from those held."""
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as err:
        raise TrustedPublishingError(
            "the token has no usable header", detail=str(err)
        ) from err

    if not isinstance(kid, str) or not kid:
        raise TrustedPublishingError("the token does not name a signing key")

    with _KEY_LOCK:
        keys = _KEYS.get(issuer)

    if keys is None:
        raise TrustedPublishingError(
            "the keys of the token's issuer are not loaded yet, try again shortly",
            detail=f"no keys yet for {issuer}",
            retryable=True,
        )

    key = _named_key(keys, kid)
    if key is None:
        raise TrustedPublishingError(
            "the token's issuer does not publish the key it names",
            detail=f"{issuer} has no key {kid!r}",
            retryable=True,
        )

    return key


def verify_token(token: str) -> dict[str, Any]:
    """Check a token's signature and registered claims, and return its claims.

    This needs nothing from the feedstock, so a caller can refuse a token that
    is not genuine before reading anything on its behalf. authorize() then
    decides whether the claims may publish a particular feedstock. What is
    raised may be shown to whoever sent the token; its detail is logged here.
    """
    try:
        return _verify_token(token)
    except TrustedPublishingError as err:
        LOGGER.info("refused a trusted publishing token: %s (%r)", err, err.detail)
        raise


def _verify_token(token: str) -> dict[str, Any]:
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError as err:
        raise TrustedPublishingError(
            "the token could not be read", detail=str(err)
        ) from err

    # read before the signature is checked, since it selects the key, but only
    # to choose among the issuers on the allowlist
    issuer = unverified.get("iss")
    if not isinstance(issuer, str) or issuer not in ALLOWED_ISSUERS:
        raise TrustedPublishingError(
            "the token's issuer is not one conda-forge accepts tokens from",
            detail=f"iss {issuer!r}",
        )

    signing_key = _signing_key(issuer, token)

    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=ALGORITHMS,
            audience=AUDIENCE,
            issuer=issuer,
            leeway=LEEWAY,
            options={"require": REQUIRED_CLAIMS},
        )
    # the two a maintainer setting up a workflow is likely to meet get a
    # message of their own; pyjwt's text only goes to the log
    except jwt.ExpiredSignatureError as err:
        raise TrustedPublishingError("the token has expired", detail=str(err)) from err
    except jwt.InvalidAudienceError as err:
        raise TrustedPublishingError(
            f"the token's audience is not {AUDIENCE}", detail=str(err)
        ) from err
    except jwt.PyJWTError as err:
        raise TrustedPublishingError("the token is not valid", detail=str(err)) from err

    if not isinstance(claims["jti"], str) or not claims["jti"]:
        raise TrustedPublishingError(
            "the token is not valid", detail=f"jti {claims['jti']!r}"
        )

    if _wall() > _expires(claims) + LEEWAY:
        raise TrustedPublishingError(
            f"the token was issued more than {MAX_TOKEN_AGE // 60} minutes ago, "
            "and is taken for no longer than that whatever its expiry says. On "
            "GitLab it is issued as the job starts, so send it sooner after that",
            detail=f"iat {claims['iat']}, exp {claims['exp']}",
        )

    # refused here as well as when it is spent, so that a used token costs
    # the caller nothing more
    with _SPENT_LOCK:
        if (claims["iss"], claims["jti"]) in _SPENT:
            raise TrustedPublishingError(
                "the token has already been used", detail=f"jti {claims['jti']!r}"
            )

    return claims


def _expires(claims: dict[str, Any]) -> float:
    """When we stop taking a token: its exp, or MAX_TOKEN_AGE after issue."""
    return min(claims["exp"], claims["iat"] + MAX_TOKEN_AGE)


def _missing_claims(claims: dict[str, Any], publisher: TrustedPublisher) -> set[str]:
    """What a provider of this publisher's kind should have sent and did not.

    A floor on the provider rather than the set we match on: `ref` and
    `repository_owner` date the instance and are not compared.
    """
    return REQUIRED_PROVIDER_CLAIMS[type(publisher)] - claims.keys()


def _is_true(value: Any) -> bool:
    """GitLab sends its booleans as strings."""
    return str(value).lower() == "true"


def issuer_of(publisher: TrustedPublisher) -> str:
    """The issuer a publisher expects its tokens from."""
    if isinstance(publisher, GitHubTrustedPublisher):
        return GITHUB_ISSUER
    return publisher.url


def _same_name(claim: Any, name: str) -> bool:
    """Case-insensitive, which is safe because the owner is pinned by id."""
    return isinstance(claim, str) and claim.casefold() == name.casefold()


def _matches_github(claims: dict[str, Any], publisher: GitHubTrustedPublisher) -> bool:
    if claims["iss"] != GITHUB_ISSUER:
        return False

    if not _same_name(claims.get("repository"), publisher.repository):
        return False

    # the entry pins a number while the claim arrives as a string
    if str(claims.get("repository_owner_id")) != str(publisher.repository_owner_id):
        return False

    # job_workflow_ref names the workflow holding the job, where workflow_ref
    # is only the one the run started from, which may call anything. The file
    # is a path in git, so it keeps its case.
    job_workflow_ref = claims.get("job_workflow_ref")
    if not isinstance(job_workflow_ref, str):
        return False
    repository, found, workflow = job_workflow_ref.partition("/.github/workflows/")
    if not found or not _same_name(repository, publisher.repository):
        return False
    if not workflow.startswith(f"{publisher.workflow}@"):
        return False

    if publisher.environment is not None:
        return _same_name(claims.get("environment"), publisher.environment)

    return True


def _matches_gitlab(claims: dict[str, Any], publisher: GitLabTrustedPublisher) -> bool:
    if claims["iss"] != publisher.url:
        return False

    if not _same_name(claims.get("project_path"), publisher.project_path):
        return False

    if str(claims.get("namespace_id")) != str(publisher.namespace_id):
        return False

    if publisher.ref_type is not None and claims.get("ref_type") != publisher.ref_type:
        return False

    if publisher.ref_protected and not _is_true(claims.get("ref_protected")):
        return False

    if publisher.environment is not None:
        return claims.get("environment") == publisher.environment

    return True


MATCHERS = {
    GitHubTrustedPublisher: _matches_github,
    GitLabTrustedPublisher: _matches_gitlab,
}

# A floor on how old a provider may be, since these have all been sent for
# years. An instance missing them has also missed its own security fixes.
REQUIRED_PROVIDER_CLAIMS = {
    GitHubTrustedPublisher: {
        "repository",
        "repository_owner",
        "repository_owner_id",
        "job_workflow_ref",
        "ref",
        "ref_type",
    },
    GitLabTrustedPublisher: {
        "project_path",
        "namespace_path",
        "namespace_id",
        "ref",
        "ref_type",
        "ref_protected",
    },
}


def _not_allowed(publisher: TrustedPublisher) -> str | None:
    """Why the allowlist will not let this publisher be used, if it will not."""
    issuer = issuer_of(publisher)
    allowed = ALLOWED_ISSUERS.get(issuer)
    if allowed is None:
        return f"conda-forge does not accept tokens from {issuer}"

    # otherwise a gitlab entry could name github's issuer
    if allowed.provider != publisher.provider:
        return (
            f"{issuer} is a {allowed.provider} issuer, not a {publisher.provider} one"
        )

    return None


def parse_publishers(entries: Any) -> list[TrustedPublisher]:
    """Read the trusted_publishers of a feedstock's conda-forge.yml.

    An entry that cannot be used is dropped with a warning rather than
    refusing the lot, so a feedstock naming a provider only a later
    webservices knows still publishes from the rest.
    """
    if not isinstance(entries, list):
        # a missing key is the ordinary case; anything else was written wrong
        if entries is not None:
            LOGGER.warning("ignoring trusted_publishers, which is not a list")
        return []

    publishers: list[TrustedPublisher] = []

    for entry in entries:
        try:
            publisher = _PUBLISHER.validate_python(entry)
        except pydantic.ValidationError as err:
            LOGGER.warning("ignoring a trusted publisher that does not parse: %s", err)
            continue

        if type(publisher) not in MATCHERS:
            LOGGER.warning(
                "ignoring a %s trusted publisher, which this version cannot check",
                publisher.provider,
            )
            continue

        reason = _not_allowed(publisher)
        if reason is not None:
            LOGGER.warning("ignoring a trusted publisher: %s", reason)
            continue

        publishers.append(publisher)

    return publishers


def describe(claims: dict[str, Any]) -> str:
    """A short, attested description of which job a token came from."""
    issuer = claims["iss"]
    if issuer == GITHUB_ISSUER:
        return f"{claims.get('job_workflow_ref')} (run {claims.get('run_id')})"

    host = issuer.removeprefix("https://")
    return f"{host}/{claims.get('project_path')} " + (
        f"({claims.get('ref_type')} {claims.get('ref')})"
    )


# The tokens authorize has accepted, kept until they expire so that one that
# leaks after use cannot be used again. This holds within one process, which
# is how the webapp runs, and a restart forgets them.
_SPENT_LOCK = threading.Lock()
_SPENT: dict[tuple[str, str], float] = {}


def _spend(claims: dict[str, Any]) -> None:
    now = _wall()
    key = (claims["iss"], claims["jti"])
    with _SPENT_LOCK:
        for spent, expires in list(_SPENT.items()):
            if expires + LEEWAY < now:
                del _SPENT[spent]
        if key in _SPENT:
            raise TrustedPublishingError(
                "the token has already been used", detail=f"jti {claims['jti']!r}"
            )
        _SPENT[key] = _expires(claims)


def authorize(
    claims: dict[str, Any], publishers: list[TrustedPublisher]
) -> TrustedPublisher:
    """Match the claims verify_token() returned to a feedstock's publishers.

    Returns the publisher that matched and spends the token, so that the same
    one is refused from then on. What is raised may be shown to whoever sent
    the token; its detail is logged here.
    """
    try:
        return _authorize(claims, publishers)
    except TrustedPublishingError as err:
        LOGGER.info("refused a trusted publishing token: %s (%r)", err, err.detail)
        raise


def _authorize(
    claims: dict[str, Any], publishers: list[TrustedPublisher]
) -> TrustedPublisher:
    # again, for publishers that did not come through parse_publishers
    allowed = []
    for publisher in publishers:
        reason = _not_allowed(publisher)
        if reason is None:
            allowed.append(publisher)
        else:
            LOGGER.warning("ignoring a trusted publisher: %s", reason)
    publishers = allowed

    if not publishers:
        raise TrustedPublishingError("this feedstock has no trusted publishers")

    named = [
        publisher for publisher in publishers if issuer_of(publisher) == claims["iss"]
    ]
    if not named:
        raise TrustedPublishingError(
            "the token's issuer is not one this feedstock publishes from",
            detail=f"iss {claims['iss']!r}",
        )

    usable = [
        publisher for publisher in named if not _missing_claims(claims, publisher)
    ]

    # a token too old to check reads differently from one that does not match
    if not usable:
        missing = set().union(
            *(_missing_claims(claims, publisher) for publisher in named)
        )
        raise TrustedPublishingError(
            f"the token does not carry {', '.join(sorted(missing))}"
        )

    for publisher in usable:
        if MATCHERS[type(publisher)](claims, publisher):
            _spend(claims)
            LOGGER.info("authorized a request from %s", describe(claims))
            return publisher

    # describe() repeats names the sender chose, such as a branch
    raise TrustedPublishingError(
        "the token matches no trusted publisher", detail=describe(claims)
    )
