import logging

from conda_forge_feedstock_ops.container_utils import ContainerRuntimeError
from conda_forge_feedstock_ops.convert import convert_feedstock_to_v1
from .utils import run_git_command

LOGGER = logging.getLogger(__name__)


def convert_to_v1(git_repo):
    LOGGER.info("converting feedstock to v1")

    info_message = None

    curr_head = git_repo.active_branch.commit

    try:
        changed = convert_feedstock_to_v1(
            git_repo.working_dir,
            use_container=True,
        )
    except ContainerRuntimeError as e:
        LOGGER.exception("Converting feedstock to v1 failed: %r", e)
        info_message = repr(e)
        ret = 1
    else:
        ret = 0
        if changed:
            run_git_command(
                "add",
                "-f",
                ".",
                cwd=git_repo.working_dir,
                check=False,
            )
            run_git_command(
                "commit",
                "--all",
                "-m",
                "chore: converted feedstock to v1",
                cwd=git_repo.working_dir,
                check=False,
            )

    if ret:
        changed, convert_error = False, True
    elif git_repo.active_branch.commit == curr_head:
        changed, convert_error = False, False
    else:
        changed, convert_error = True, False

    return changed, convert_error, info_message
