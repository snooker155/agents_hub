"""`ah setup` (also `ah onboard`): the guided install, see wizard.py."""
from __future__ import annotations

import sys
from typing import Optional

import typer


def setup_command(
    shape: Optional[str] = typer.Option(
        None, "--shape", help="local, docker, compose, kubernetes or remote. Asked when omitted."),
    answers: Optional[str] = typer.Option(
        None, "--answers", help="A JSON file with the answers, for an unattended install. Nothing is asked."),
    directory: Optional[str] = typer.Option(
        None, "--dir", help="The docker shape: the folder for docker-compose.yml and .env."),
    advanced: bool = typer.Option(False, "--advanced", help="Skip the QuickStart question and ask everything."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Stop after the review; write nothing."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Apply without the final confirmation."),
    no_start: bool = typer.Option(False, "--no-start", help="Configure only; do not offer to start the hub."),
    in_docker: bool = typer.Option(
        False, "--in-docker", hidden=True,
        help="Run from the image's setup command: write into --workdir and start the stack through the socket."),
    workdir: Optional[str] = typer.Option(
        None, "--workdir", hidden=True, help="With --in-docker: the folder the container was started in."),
    disconnect: bool = typer.Option(
        False, "--disconnect", help="Forget the hub a remote setup saved, so `ah` runs this checkout again."),
):
    """Install and configure the hub step by step: how it runs, how much load
    it carries, the database, who signs in, providers and default models,
    features. Run it again to
    change any of it; every question then defaults to the value in force."""
    # Imported here, not at the top: cli.main imports this module while it is
    # itself still loading, and the steps import cli.main for the CLI state.
    from cli.main import console
    from cli.onboard.ui import AnswersAsker, SetupError, TerminalAsker
    from cli.onboard import wizard

    if disconnect:
        if wizard._saved_remote():
            wizard._forget_remote()
            console.print("Forgot the saved hub; `ah` runs this checkout again.")
        else:
            console.print("No hub is saved.")
        return

    if in_docker and workdir:
        # The image's launcher changes into /app before starting Python, so a
        # relative path the person typed means their folder, not that one.
        from pathlib import Path
        if answers and not Path(answers).is_absolute():
            answers = str(Path(workdir) / answers)
        if directory and not Path(directory).is_absolute():
            directory = str(Path(workdir) / directory)
    try:
        if answers:
            asker = AnswersAsker.from_file(console, answers)
        elif not sys.stdin.isatty():
            console.print("[red]No terminal to ask on.[/red] Pass [bold]--answers FILE[/bold] "
                          "(see docs/installation.md, \"Guided setup\").")
            raise typer.Exit(2)
        else:
            asker = TerminalAsker(console)
        code = wizard.run(asker, shape=shape, directory=directory, advanced=advanced,
                          dry_run=dry_run, yes=yes, no_start=no_start, in_docker=in_docker, workdir=workdir)
    except SetupError as exc:
        console.print(f"[red]Setup stopped:[/red] {exc}")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped. Anything not yet applied was left unchanged.[/dim]")
        raise typer.Exit(130)
    raise typer.Exit(code)
