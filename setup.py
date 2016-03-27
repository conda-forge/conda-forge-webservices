#!/usr/bin/env python
from setuptools import setup, find_packages


def main():
    skw = dict(
        name='conda-forge-webservices',
        version='1.0',
        author='Phil Elson',
        author_email='pelson.pub@gmail.com',
        url='https://github.com/conda-forge/conda-forge-webservices',
        # entry_points=dict(console_scripts=[
        #                    'conda_forge_webservices.linting = conda_forge_webservices.linting:main']),
        packages=find_packages(),
        include_package_data=True,
        )
    setup(**skw)


if __name__ == '__main__':
    main()
