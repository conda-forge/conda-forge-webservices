import shutil
import textwrap
from pathlib import Path


from conda_forge_webservices.linting import compute_lint_message, lint_all_recipes


def data_folder():
    return Path(__file__).parent / "data"


def test_skip_ci_recipe(skip_if_linting_via_gha):
    lint = compute_lint_message(
        "conda-forge", "conda-forge-webservices", 58, set_pending_status=False
    )
    assert lint is None


def test_skip_lint_recipe(skip_if_linting_via_gha):
    lint = compute_lint_message(
        "conda-forge", "conda-forge-webservices", 59, set_pending_status=False
    )
    assert lint is None


def test_ci_skip_recipe(skip_if_linting_via_gha):
    lint = compute_lint_message(
        "conda-forge", "conda-forge-webservices", 65, set_pending_status=False
    )
    assert lint is None


def test_lint_skip_recipe(skip_if_linting_via_gha):
    lint = compute_lint_message(
        "conda-forge", "conda-forge-webservices", 66, set_pending_status=False
    )
    assert lint is None


def test_good_recipe(skip_if_linting_via_gha):
    # a message similar to this comes out
    """
    Hi! This is the friendly automated conda-forge-linting service.

    I just wanted to let you know that I linted all conda-recipes in your PR (```recipes/good_recipe/meta.yaml```) and found it was in an excellent condition.

    """  # noqa

    lint = compute_lint_message(
        "conda-forge", "conda-forge-webservices", 16, set_pending_status=False
    )
    assert lint is not None, lint["message"]
    assert "found it was in an excellent condition." in lint["message"], lint["message"]


def test_ok_recipe_above_good_recipe(skip_if_linting_via_gha):
    expected_message = textwrap.dedent("""
    Hi! This is the friendly automated conda-forge-linting service.

    I just wanted to let you know that I linted all conda-recipes in your PR (```recipe/meta.yaml```, ```recipes/recipe/meta.yaml```) and found it was in an excellent condition.

    """)  # noqa

    lint = compute_lint_message(
        "conda-forge", "conda-forge-webservices", 54, set_pending_status=False
    )
    assert lint["message"].startswith(expected_message)


def test_ok_recipe_beside_good_recipe(skip_if_linting_via_gha):
    expected_message = textwrap.dedent("""
    Hi! This is the friendly automated conda-forge-linting service.

    I just wanted to let you know that I linted all conda-recipes in your PR (```recipe/blah/meta.yaml```, ```recipe/meta.yaml```, ```recipes/recipe/meta.yaml```) and found it was in an excellent condition.

    """)  # noqa

    lint = compute_lint_message(
        "conda-forge", "conda-forge-webservices", 62, set_pending_status=False
    )
    assert lint["message"].startswith(expected_message)


def test_ok_recipe_above_ignored_good_recipe(skip_if_linting_via_gha):
    expected_message = textwrap.dedent("""
    Hi! This is the friendly automated conda-forge-linting service.

    I just wanted to let you know that I linted all conda-recipes in your PR (```recipe/meta.yaml```) and found it was in an excellent condition.

    """)  # noqa

    lint = compute_lint_message(
        "conda-forge", "conda-forge-webservices", 54, True, set_pending_status=False
    )
    assert lint["message"].startswith(expected_message)


def test_ok_recipe_beside_ignored_good_recipe(skip_if_linting_via_gha):
    expected_message = textwrap.dedent("""
    Hi! This is the friendly automated conda-forge-linting service.

    I just wanted to let you know that I linted all conda-recipes in your PR (```recipe/blah/meta.yaml```, ```recipe/meta.yaml```) and found it was in an excellent condition.

    """)  # noqa

    lint = compute_lint_message(
        "conda-forge", "conda-forge-webservices", 62, True, set_pending_status=False
    )
    assert lint["message"].startswith(expected_message)


def test_conflict_ok_recipe(skip_if_linting_via_gha):
    expected_message = textwrap.dedent("""
    Hi! This is the friendly automated conda-forge-linting service.

    I was trying to look for recipes to lint for you, but it appears we have a merge conflict.
    Please try to merge or rebase with the base branch to resolve this conflict.

    Please ping the 'conda-forge/core' team (using the `@` notation in a comment) if you believe this is a bug.
    """)  # noqa

    lint = compute_lint_message(
        "conda-forge", "conda-forge-webservices", 56, set_pending_status=False
    )
    assert lint is not None, lint["message"]
    assert expected_message == lint["message"]


def test_conflict_2_ok_recipe(skip_if_linting_via_gha):
    expected_message = textwrap.dedent("""
    Hi! This is the friendly automated conda-forge-linting service.

    I was trying to look for recipes to lint for you, but it appears we have a merge conflict.
    Please try to merge or rebase with the base branch to resolve this conflict.

    Please ping the 'conda-forge/core' team (using the `@` notation in a comment) if you believe this is a bug.
    """)  # noqa

    lint = compute_lint_message(
        "conda-forge", "conda-forge-webservices", 57, set_pending_status=False
    )
    assert lint is not None, lint["message"]
    assert expected_message == lint["message"]


def test_v1_recipe(skip_if_linting_via_gha):
    expected = (
        "I wanted to let you know that I linted all conda-recipes in your PR "
        "(```recipe/recipe.yaml```) and found some lint."
    )
    lint = lint_all_recipes(data_folder(), [])
    lint = compute_lint_message(
        "conda-forge", "conda-forge-webservices", 632, set_pending_status=False
    )
    assert lint is not None, lint["message"]
    assert expected in lint["message"], lint["message"]


def test_bad_recipe(skip_if_linting_via_gha):
    # a message similar to this comes out
    """
    Hi! This is the friendly automated conda-forge-linting service.

    I wanted to let you know that I linted all conda-recipes in your PR (```recipes/bad_recipe/meta.yaml```) and found some lint.

    Here's what I've got...


    For **recipes/bad_recipe/meta.yaml**:

        * The home item is expected in the about section.
        * The license item is expected in the about section.
        * The summary item is expected in the about section.
        * The recipe must have some tests.
        * The recipe must have a `build/number` section.
        * There are 2 too many lines.  There should be one empty line at the end of the file.
        * Feedstock with the same name exists in conda-forge
        * Recipe maintainer "support" does not exist
    """  # noqa

    lint = compute_lint_message(
        "conda-forge", "conda-forge-webservices", 17, set_pending_status=False
    )
    assert lint is not None, lint["message"]
    assert "found some lint" in lint["message"], lint["message"]
    assert "The home item is expected in the about section." in lint["message"], lint[
        "message"
    ]
    assert "For **recipes/bad_recipe/meta.yaml**:" in lint["message"], lint["message"]


def test_mixed_recipe(skip_if_linting_via_gha):
    # a message similar to this comes out
    """
    Hi! This is the friendly automated conda-forge-linting service.

    I just wanted to let you know that I linted all conda-recipes in your PR (```recipes/hints_only/meta.yaml```) and found it was in an excellent condition.


    I do have some suggestions for making it better though...


    For **recipes/hints_only/meta.yaml**:

        * Whenever possible python packages should use pip. See https://conda-forge.org/docs/maintainer/adding_pkgs.html#use-pip
    """  # noqa

    lint = compute_lint_message(
        "conda-forge", "conda-forge-webservices", 217, set_pending_status=False
    )
    assert lint is not None, lint["message"]
    assert (
        "I do have some suggestions for making it better though" in lint["message"]
    ), lint["message"]


def test_no_recipe(skip_if_linting_via_gha):
    expected_message = textwrap.dedent("""
    Hi! This is the friendly automated conda-forge-linting service.

    I was trying to look for recipes to lint for you, but couldn't find any.
    Please ping the 'conda-forge/core' team (using the `@` notation in a comment) if you believe this is a bug.
    """)  # noqa

    lint = compute_lint_message(
        "conda-forge", "conda-forge-webservices", 523, set_pending_status=False
    )
    assert lint is not None, lint["message"]
    assert expected_message == lint["message"]


def test_closed_pr(skip_if_linting_via_gha):
    lint = compute_lint_message(
        "conda-forge", "conda-forge-webservices", 52, set_pending_status=False
    )
    assert lint is None


def test_new_recipe(tmp_path, skip_if_linting_via_gha):
    recipe_file = tmp_path / "recipe" / "recipe.yaml"
    recipe_file.parent.mkdir(parents=True)
    shutil.copy(data_folder() / "recipe.yaml", recipe_file)

    message, status = lint_all_recipes(Path(tmp_path), [])
    assert status == "bad"
    assert (
        "I wanted to let you know that I linted all conda-recipes in your PR "
        "(```recipe/recipe.yaml```) and found some lint." in message
    )
