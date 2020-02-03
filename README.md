FALALALA
# conda-forge-webservices
[![Build Status](https://travis-ci.com/conda-forge/conda-forge-webservices.svg?branch=master)](https://travis-ci.com/conda-forge/conda-forge-webservices)[![CircleCI](https://circleci.com/gh/conda-forge/conda-forge-webservices.svg?style=svg)](https://circleci.com/gh/conda-forge/conda-forge-webservices)

This repository is the source for the Heroku hosted webapp which powers the conda-forge-admin
commands and lints conda-forge's recipes. The linting itself comes from conda-smithy
(https://github.com/conda-forge/conda-smithy).

## Configuration
Rather than using OAuth, this app is using a pre-determined "personal access token" which has
appropriate conda-forge permissions. It has been configured with:

    heroku config:set GH_TOKEN=<token>

The service deploys to the "conda-forge" heroku project: https://dashboard.heroku.com/apps/conda-forge/resources

It is then a case of adding the appropriate webhook to trigger the service on ``pull_request``.

The buildpack for this repo comes from https://github.com/pl31/heroku-buildpack-conda, which allows a conda
environment to be deployed as part of the slug.

## Testing

The tests for this repo require a GitHub API key which is not available on forks. Thus the tests only pass
for PRs made from a branch in this repo. If your PR is in a fork, please ask a member of `@conda-forge/core`
to push your PR branch to the main repo to enable the tests.
