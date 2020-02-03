import os
import subprocess


def update(token=None):
    if token is None:
        token = os.environ["STATUS_GH_TOKEN"]

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
