#!/usr/bin/env python
from setuptools import setup, find_packages


def main():
    skw = dict(
        name='conda-forge-webservices',
        version='1.0',
        author='Phil Elson',
        author_email='pelson.pub@gmail.com',
        url='https://github.com/conda-forge/conda-forge-webservices',
        entry_points={
            "console_scripts": [
                'update-webservices=conda_forge_webservices.update_me:main',
                'cache-status-data=conda_forge_webservices.status_monitor:cache_status_data',  # noqa
            ],
        },
        packages=find_packages(),
        include_package_data=True,
        )
    setup(**skw)


if __name__ == '__main__':
    main()
