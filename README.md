# conda-forge-webservices
[![tests](https://github.com/conda-forge/conda-forge-webservices/actions/workflows/tests.yml/badge.svg?event=merge_group)](https://github.com/conda-forge/conda-forge-webservices/actions/workflows/tests.yml) [![clean-and-update](https://github.com/conda-forge/conda-forge-webservices/workflows/clean-and-update/badge.svg)](https://github.com/conda-forge/conda-forge-webservices/actions?query=workflow%3Aclean-and-update)

This repository is the source for the Heroku hosted webapp which powers the conda-forge-admin
commands and lints conda-forge's recipes. The linting itself comes from conda-smithy
(https://github.com/conda-forge/conda-smithy).

## Configuration
This app generates GitHub App tokens for the conda-forge-webservices[bot] in order to function. It also
uses a single machine user with no special permissions in order to make forks for rerendering. Ask a member of
`@conda-forge/core` for details if you need them.

## Testing

The tests for this repo require a GitHub API key which is not available on forks. We use a merge queue to handle this.
The tests in your PR will run, but some of them will be skipped. Once the PR is merged, it will be put into a queue on the
upstream repo for complete testing. If it passes, it will be merged. If it does not pass, the PR will be kicked out of the
queue and we will have to try again. Only maintainers on the upstream repo can add tests to the merge queue. You can
bump `@conda-forge/core` for a review and merge into the queue.
