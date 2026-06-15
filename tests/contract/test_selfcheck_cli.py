import inspect

import src.cli as cli


def test_cli_exposes_selfcheck_entrypoint():
    assert hasattr(cli, "run_dry_selfcheck")
    # signature mirrors run_dry_interpret (same dependencies wired in)
    assert list(inspect.signature(cli.run_dry_selfcheck).parameters.keys()) == list(
        inspect.signature(cli.run_dry_interpret).parameters.keys()
    )
