# webservices-dispatch-action

a docker image to run conda-forge's admin webservices GitHub Actions integrations

## Description

This image contains the code and integrations to run conda-forge's webservices GitHub Actions
integrations. Those integrations perform basic tasks like rerendering feedstocks and updating
to new versions.

## License

This image is licensed under [BSD-3 Clause](https://github.com/conda-forge/conda-forge-webservices/blob/main/LICENSE).

## Documentation & Contributing

You can find documentation for how to use the image on the
upstream [repo](https://github.com/conda-forge/conda-forge-webservices) and in the sections below.

To get in touch with the maintainers of this image, please [make an issue](https://github.com/conda-forge/conda-forge-webservices/issues/new/choose)
and bump the `@conda-forge/core` team.

Contributions are welcome in accordance
with conda-forge's [code of conduct](https://conda-forge.org/community/code-of-conduct/). We accept them through pull requests on the
upstream [repo](https://github.com/conda-forge/conda-forge-webservices/compare).

## Important Image Tags

- `latest`: the current production image, tag is not used but is there for convenience
- `YYYY.MM.R[dev tag info]`: tags for each release, include test images, format is calver with a patch number `R` that increases by one for each release in a given month

## Getting Started & Usage

This image is intended to be called via the code in the [conda-forge/conda-forge-feedstock-ops](https://github.com/conda-forge/conda-forge-feedstock-ops) package. It is not intended to be used directly. See that package for more information.
