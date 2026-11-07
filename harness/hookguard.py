"""While a hook runs, nothing may run the project's own code: its gate command, its tests, its imports or its build.

Claude Code runs hooks outside the sandbox it puts the agent's commands in, so code a hook starts can write where the
agent cannot (E2E-SWE install run, Horizon 67c11e6: the SessionStart hook ran the agents' gates, which ran their
setup.sh, which wrote into the task's environment and into the system Python). A hook reads saved results; the agent's
own `check` and `close`, inside its sandbox, run the code."""
import contextlib

_depth = 0


class Refused(Exception):
    """Raised instead of running the project's code while a hook runs."""


@contextlib.contextmanager
def active():
    global _depth
    _depth += 1
    try:
        yield
    finally:
        _depth -= 1


def refuse(what: str) -> None:
    if _depth:
        raise Refused(f"{what} runs the project's code, and a hook never does (hooks run outside the sandbox); "
                      "`check` and `close` run it")
