import glob
import os
import shutil

import pytest


@pytest.fixture(scope="session", autouse=True)
def prep_test_recipes():
    recipe_data = os.path.join(
        os.path.dirname(__file__),
        "data"
    )
    recipes = glob.glob(os.path.join(recipe_data, "recipes", "*.yaml.skipme"))
    for recipe in recipes:
        shutil.move(recipe, recipe.replace(".skipme", ""))
    yield
    for recipe in recipes:
        shutil.move(recipe.replace(".skipme", ""), recipe)
