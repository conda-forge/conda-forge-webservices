import os
import subprocess


def get_token(token=None):
    if token is None:
        token = os.environ["STATUS_GH_TOKEN"]

    return token


def upgrade(token=None):
    token = get_token(token=token)

    subprocess.check_call([
        "statuspage",
        "upgrade",
        "--org",
        "conda-forge",
        "--name",
        "status",
        "--token",
        token
    ])


def update(token=None):
    token = get_token(token=token)

    subprocess.check_call([
        "statuspage",
        "update",
        "--org",
        "conda-forge",
        "--name",
        "status",
        "--token",
        token
    ])


def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="status",
        description="Updates the status page.",
    )

    update(os.environ["STATUS_GH_TOKEN"])


if __name__ == '__main__':
    main()
