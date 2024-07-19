import glob
import os
import shutil

import pytest


@pytest.fixture(scope="session", autouse=True)
def prep_test_recipes():
    recipe_data = os.path.join(
        os.path.dirname(__file__),
        "linting",
        "data"
    )
    recipes = glob.glob(os.path.join(recipe_data, "*.yaml.skipme"))
    for recipe in recipes:
        shutil.move(recipe, recipe.replace(".skipme", ""))
    yield
    for recipe in recipes:
        shutil.move(recipe.replace(".skipme", ""), recipe)
