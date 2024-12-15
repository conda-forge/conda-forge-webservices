from conda_forge_webservices.update_teams import get_recipe_dummy_meta

import pytest


@pytest.mark.parametrize("recipe_content, res", [
    (
        """\
{% set version = "0.1" %}

blah: five

extra:
    recipe-maintainers:
        - a
        - b
        - c
""",
        {"a", "b", "c"},
    ),
    (
        """\
{% set version = "0.1" %}

extra:
    feedstock-name: {{ name }}
    recipe-maintainers:
        - a
        - b
""",
        {"a", "b"},
    ),
    (
        """\
{% set version = "0.1" %}

extra:
    feedstock-name: ${{ name }}
    recipe-maintainers:
        - a
        - d
""",
        {"a", "d"},
    )
])
def test_get_recipe_dummy_meta(recipe_content, res):
    meta = get_recipe_dummy_meta(recipe_content)
    assert set(meta.meta["extra"]["recipe-maintainers"]) == res
