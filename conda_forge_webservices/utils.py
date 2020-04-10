import os
import shutil
import tempfile
from contextlib import contextmanager


@contextmanager
def tmp_directory():
    tmp_dir = tempfile.mkdtemp('_recipe')
    yield tmp_dir
    shutil.rmtree(tmp_dir)


# https://stackoverflow.com/questions/6194499/pushd-through-os-system
@contextmanager
def pushd(new_dir):
    previous_dir = os.getcwd()
    os.chdir(new_dir)
    try:
        yield
    finally:
        os.chdir(previous_dir)
