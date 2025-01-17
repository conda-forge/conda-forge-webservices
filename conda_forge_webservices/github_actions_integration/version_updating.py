import logging
import os
import pprint
import subprocess
from pathlib import Path

from conda.models.version import VersionOrder

from .api_sessions import create_api_sessions


LOGGER = logging.getLogger(__name__)


def update_version(
    git_repo, repo_name, input_version=None
) -> tuple[bool, bool, str | None]:
    """
    Returns [whether version changed, errors occurred, new version found]
    """
    # these imports are guarded here in this function since the
    # conda_forge_tick package will hide sensitive env vars
    import conda_forge_tick.update_recipe
    from conda_forge_tick.feedstock_parser import load_feedstock
    from conda_forge_tick.update_recipe.version import update_version_feedstock_dir
    from conda_forge_tick.update_upstream_versions import (
        all_version_sources,
        get_latest_version,
    )
    from conda_forge_tick.utils import setup_logging

    setup_logging()

    name = os.path.basename(repo_name).rsplit("-", 1)[0]
    LOGGER.info("using feedstock name %s for repo %s", name, repo_name)

    try:
        LOGGER.info("computing feedstock attributes")
        attrs = load_feedstock(name, {}, use_container=True)
        LOGGER.info("feedstock attrs:\n%s\n", pprint.pformat(attrs))
    except Exception:
        LOGGER.exception("error while computing feedstock attributes!")
        return False, True, None

    if input_version is None or input_version == "null":
        try:
            LOGGER.info("getting latest version")
            new_version = get_latest_version(
                name,
                attrs,
                all_version_sources(),
                use_container=True,
            )
            new_version = new_version["new_version"]
            if new_version:
                LOGGER.info(
                    "curr version|latest version: %s|%s",
                    attrs.get("version", "0.0.0"),
                    new_version,
                )
            else:
                raise RuntimeError("Could not fetch latest version!")
        except Exception:
            LOGGER.exception("error while getting feedstock version!")
            return False, True, None
    else:
        LOGGER.info("using input version")
        new_version = input_version
        LOGGER.info(
            "curr version|input version: %s|%s",
            attrs.get("version", "0.0.0"),
            new_version,
        )

    # if we are finding the version automatically, check that it is going up
    if (input_version is None or input_version == "null") and (
        VersionOrder(str(new_version).replace("-", "."))
        <= VersionOrder(str(attrs.get("version", "0.0.0")).replace("-", "."))
    ):
        LOGGER.info(
            "not updating since new version is less or equal to current version"
        )
        return False, False, new_version

    schema_version = 0
    try:
        updated, errors = update_version_feedstock_dir(
            git_repo.working_dir,
            str(new_version),
            use_container=True,
        )
        if errors or (not updated):
            LOGGER.critical("errors when updating the recipe: %r", errors)
            raise RuntimeError("Error updating the recipe!")

        # no container used here since this is a pure text-based operation
        # with a regex
        workdir = Path(git_repo.working_dir)
        meta_yaml_path = workdir.joinpath("recipe", "meta.yaml")
        recipe_yaml_path = workdir.joinpath("recipe", "recipe.yaml")
        if meta_yaml_path.exists():
            new_meta_yaml = meta_yaml_path.read_text()
            new_meta_yaml = conda_forge_tick.update_recipe.update_build_number(
                new_meta_yaml,
                0,
            )
            meta_yaml_path.write_text(new_meta_yaml)
        elif recipe_yaml_path.exists():
            conda_forge_tick.update_recipe.v1_recipe.update_build_number(
                recipe_yaml_path,
                0,
            )
            schema_version = 1
        else:
            raise FileNotFoundError("Could not find meta.yaml or recipe.yaml!")

    except Exception:
        LOGGER.exception("error while updating the recipe!")
        return False, True, new_version

    try:
        recipe_path = (
            "recipe/meta.yaml" if schema_version == 0 else "recipe/recipe.yaml"
        )
        subprocess.run(
            ["git", "add", recipe_path],
            cwd=git_repo.working_dir,
            check=True,
            env=os.environ,
        )

        subprocess.run(
            ["git", "commit", "-m", f"ENH updated version to {new_version}"],
            cwd=git_repo.working_dir,
            check=True,
            env=os.environ,
        )
    except Exception:
        LOGGER.exception("error while committing new recipe to repo")
        return False, True, new_version

    return True, False, new_version


def update_pr_title(
    repo_name: str, pr_number: int, found_version: str
) -> tuple[bool, bool]:
    """
    Returns [whether title changed, errored]
    """
    try:
        _, gh = create_api_sessions()
        repo = gh.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
    except Exception:
        LOGGER.exception(
            "error while trying to get PR title for %s#%s",
            repo_name,
            pr_number,
        )
        return False, True

    if pr.title == "ENH: update package version":  # user didn't change the default
        try:
            pr.edit(title=f"{pr.title} to {found_version}")
            return True, False
        except Exception:
            LOGGER.exception(
                "error while trying to change PR title for %s#%s",
                repo_name,
                pr_number,
            )
            return False, True

    return False, False
