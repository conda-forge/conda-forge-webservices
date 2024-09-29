import logging
import os
import subprocess

import yaml
from conda_forge_feedstock_ops.container_utils import ContainerRuntimeError
from conda_forge_feedstock_ops.rerender import rerender as cf_feedstock_ops_rerender

LOGGER = logging.getLogger(__name__)


def rerender(git_repo):
    LOGGER.info("rerendering")

    info_message = None

    _ensure_output_validation_is_on(git_repo)

    curr_head = git_repo.active_branch.commit

    try:
        msg = cf_feedstock_ops_rerender(
            git_repo.working_dir,
            timeout=None,
            use_container=True,
        )
    except ContainerRuntimeError as e:
        LOGGER.error(f"Rerendering failed: {e}")
        ret = 1
    else:
        ret = 0
        if msg is not None:
            subprocess.call(
                ["git", "add", "."],
                cwd=git_repo.working_dir,
            )
            subprocess.call(
                ["git", "commit", "--all", "-m", msg],
                cwd=git_repo.working_dir,
            )

    if ret:
        changed, rerender_error = False, True
    elif git_repo.active_branch.commit == curr_head:
        changed, rerender_error = False, False
    else:
        changed, rerender_error = True, False

    return changed, rerender_error, info_message, msg


def _ensure_output_validation_is_on(git_repo):
    pth = os.path.join(git_repo.working_dir, "conda-forge.yml")
    if os.path.exists(pth):
        with open(pth) as fp:
            cfg = yaml.safe_load(fp)
    else:
        cfg = {}

    if not cfg.get("conda_forge_output_validation", False):
        cfg["conda_forge_output_validation"] = True

        with open(pth, "w") as fp:
            fp.write(yaml.dump(cfg, default_flow_style=False))

        subprocess.run(
            ["git", "add", "conda-forge.yml"],
            cwd=git_repo.working_dir,
            env=os.environ,
        )
        return True
    else:
        return False
