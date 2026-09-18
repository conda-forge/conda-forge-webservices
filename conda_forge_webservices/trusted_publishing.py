"""Verify the identity tokens that CI providers issue to their own jobs.

A feedstock lists the jobs allowed to publish it in its conda-forge.yml, the job
sends the token its provider issued it, and this checks the token came from that
provider and matches an entry. Neither side holds a credential.

Any provider speaking OIDC can be named, including a self-managed GitLab. The
issuers we will talk to come from the feedstock's entries and never from the
token, so the addresses this can be made to fetch are only those that have been
through review on a feedstock.
"""

import ipaddress
import json
import logging
import socket
from typing import Any
from urllib.parse import urlsplit

import cachetools
import jwt
import pydantic
import requests
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

REQUIRED_CLAIMS = ["iss", "aud", "exp", "iat", "sub"]

# the entries are written in conda-forge.yml, so conda-smithy's schema is both
# what describes them to a maintainer and what validates them here
_PUBLISHER = pydantic.TypeAdapter(TrustedPublisher)

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 10
MAX_RESPONSE_BYTES = 1024 * 1024
KEY_LIFETIME = 300


class TrustedPublishingError(Exception):
    """A token could not be verified or matched no trusted publisher."""


def _reject_unroutable(host: str, port: int) -> None:
    """Refuse a host that resolves anywhere but the public internet.

    Every address it answers with is checked, not just the first, since a name
    can hand back one public and one loopback address.
    """
    try:
        resolved = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as err:
        raise TrustedPublishingError(f"{host} does not resolve: {err}") from err

    for *_, sockaddr in resolved:
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global or address.is_multicast:
            raise TrustedPublishingError(
                f"{host} resolves to {address}, which is not on the public internet"
            )


def _fetch_json(url: str) -> Any:
    """GET a URL a feedstock chose, with the usual precautions.

    https is most of the value: an internal service cannot present a
    certificate for the name being fetched, so a name that resolves somewhere
    public now and somewhere internal a moment later fails the handshake rather
    than returning anything. Redirects are refused because the hop would not be
    checked.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise TrustedPublishingError(f"{url!r} is not https")
    if parts.username or parts.password:
        raise TrustedPublishingError(f"{url!r} carries credentials")
    if not parts.hostname:
        raise TrustedPublishingError(f"{url!r} has no host")

    _reject_unroutable(parts.hostname, parts.port or 443)

    try:
        with requests.get(
            url,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            allow_redirects=False,
            stream=True,
        ) as res:
            if res.status_code != 200:
                raise TrustedPublishingError(
                    f"{url} answered {res.status_code}, not 200"
                )
            body = res.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
    except requests.RequestException as err:
        raise TrustedPublishingError(f"could not fetch {url}: {err}") from err

    if len(body) > MAX_RESPONSE_BYTES:
        raise TrustedPublishingError(f"{url} returned more than we will read")

    try:
        return json.loads(body)
    except ValueError as err:
        raise TrustedPublishingError(f"{url} did not return json: {err}") from err


@cachetools.cached(cachetools.TTLCache(maxsize=128, ttl=KEY_LIFETIME))
def _jwks(issuer: str) -> jwt.PyJWKSet:
    """Find an issuer's signing keys through OIDC discovery.

    PyJWKClient fetches with urllib, which would side step _fetch_json.
    """
    doc = _fetch_json(f"{issuer}/.well-known/openid-configuration")

    # the document has to agree about whose it is, or one issuer could hand us
    # another's keys
    if doc.get("issuer") != issuer:
        raise TrustedPublishingError(
            f"{issuer} serves a discovery document belonging to {doc.get('issuer')!r}"
        )

    jwks_uri = doc.get("jwks_uri", "")
    if not jwks_uri.startswith(f"{issuer}/"):
        raise TrustedPublishingError(
            f"{issuer} publishes its keys off its own host, at {jwks_uri!r}"
        )

    try:
        return jwt.PyJWKSet.from_dict(_fetch_json(jwks_uri))
    except jwt.PyJWTError as err:
        raise TrustedPublishingError(f"{issuer} served no usable keys: {err}") from err


def _signing_key(issuer: str, token: str) -> jwt.PyJWK:
    """The key an issuer says it signed a token with."""
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as err:
        raise TrustedPublishingError(f"the token has no usable header: {err}") from err

    for attempt in range(2):
        for key in _jwks(issuer).keys:
            if key.key_id == kid:
                return key

        # an unknown kid usually means the issuer has rotated since we last
        # looked, so drop what we have and ask once more
        if attempt == 0 and _jwks.cache is not None:
            _jwks.cache.clear()

    raise TrustedPublishingError(f"{issuer} does not publish a key {kid!r}")


def verify_token(token: str, issuers: set[str]) -> dict[str, Any]:
    """Check a token's signature and registered claims, and return its claims.

    Reading the issuer before the signature is checked cannot be avoided, since
    it selects the key. It is only used to look one up in `issuers`, so a token
    naming anywhere else is refused before a request is made on its behalf.
    """
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError as err:
        raise TrustedPublishingError(f"the token could not be read: {err}") from err

    issuer = unverified.get("iss")
    if issuer not in issuers:
        raise TrustedPublishingError(
            f"{issuer!r} is not an issuer this feedstock publishes from"
        )

    signing_key = _signing_key(issuer, token)

    try:
        return jwt.decode(
            token,
            signing_key,
            algorithms=ALGORITHMS,
            audience=AUDIENCE,
            issuer=issuer,
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.PyJWTError as err:
        raise TrustedPublishingError(f"the token is not valid: {err}") from err


def _require_complete_claims(
    claims: dict[str, Any], publishers: list[TrustedPublisher]
) -> None:
    """Refuse a provider too old to send the claims we match on."""
    for publisher in publishers:
        missing = REQUIRED_PROVIDER_CLAIMS[type(publisher)] - claims.keys()
        if not missing:
            continue

        raise TrustedPublishingError(
            f"the token does not carry {', '.join(sorted(missing))}"
        )


def _is_true(value: Any) -> bool:
    """GitLab sends its booleans as strings."""
    return str(value).lower() == "true"


def issuer_of(publisher: TrustedPublisher) -> str:
    """The issuer a publisher expects its tokens from."""
    if isinstance(publisher, GitHubTrustedPublisher):
        return GITHUB_ISSUER
    return publisher.url


def _matches_github(claims: dict[str, Any], publisher: GitHubTrustedPublisher) -> bool:
    if claims["iss"] != GITHUB_ISSUER:
        return False

    if claims.get("repository") != publisher.repository:
        return False

    # the entry pins a number while the claim arrives as a string
    if str(claims.get("repository_owner_id")) != str(publisher.repository_owner_id):
        return False

    # matching the whole "<repo>/.github/workflows/<file>@" prefix rather than
    # the file name keeps a workflow of the same name elsewhere from matching
    prefix = f"{publisher.repository}/.github/workflows/{publisher.workflow}@"
    if not str(claims.get("workflow_ref", "")).startswith(prefix):
        return False

    if publisher.environment is not None:
        return claims.get("environment") == publisher.environment

    return True


def _matches_gitlab(claims: dict[str, Any], publisher: GitLabTrustedPublisher) -> bool:
    if claims["iss"] != publisher.url:
        return False

    if claims.get("project_path") != publisher.project_path:
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
        "workflow_ref",
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


def parse_publishers(entries: Any) -> list[TrustedPublisher]:
    """Read the trusted_publishers of a feedstock's conda-forge.yml.

    An entry that cannot be read is dropped rather than taken as a reason to
    refuse the lot, so a feedstock naming a provider a later webservices knows
    about still publishes from the ones this one does. Dropping is also what
    keeps everything below total: a publisher that got this far has an issuer
    and a matcher.
    """
    publishers: list[TrustedPublisher] = []

    for entry in entries or []:
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

        publishers.append(publisher)

    return publishers


def describe(claims: dict[str, Any]) -> str:
    """A short, attested description of which job a token came from."""
    issuer = claims["iss"]
    if issuer == GITHUB_ISSUER:
        return f"{claims.get('workflow_ref')} (run {claims.get('run_id')})"

    host = issuer.removeprefix("https://")
    return f"{host}/{claims.get('project_path')} " + (
        f"({claims.get('ref_type')} {claims.get('ref')})"
    )


def authorize(
    token: str, publishers: list[TrustedPublisher]
) -> tuple[TrustedPublisher, dict[str, Any]]:
    """Verify a token against a feedstock's trusted publishers.

    Returns the publisher that matched and the token's verified claims.
    """
    if not publishers:
        raise TrustedPublishingError("this feedstock has no trusted publishers")

    claims = verify_token(token, {issuer_of(publisher) for publisher in publishers})

    _require_complete_claims(
        claims,
        [
            publisher
            for publisher in publishers
            if issuer_of(publisher) == claims["iss"]
        ],
    )

    for publisher in publishers:
        if MATCHERS[type(publisher)](claims, publisher):
            LOGGER.info("authorized a request from %s", describe(claims))
            return publisher, claims

    raise TrustedPublishingError(
        f"the token from {describe(claims)} matches no trusted publisher"
    )
