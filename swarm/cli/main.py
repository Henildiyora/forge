from __future__ import annotations

import typer

from swarm.cli.commands.approvals import grant_approval, list_approvals, reject_approval
from swarm.cli.commands.chaos import chaos
from swarm.cli.commands.connect import connect
from swarm.cli.commands.deploy import deploy
from swarm.cli.commands.init import init_project
from swarm.cli.commands.monitor import monitor
from swarm.cli.commands.status import status

app = typer.Typer(help="DevOps Swarm CLI")
approvals_app = typer.Typer(help="Approval request operations")
app.command("init")(init_project)
app.command("connect")(connect)
app.command("deploy")(deploy)
app.command("monitor")(monitor)
app.command("chaos")(chaos)
app.command("status")(status)
approvals_app.command("list")(list_approvals)
approvals_app.command("grant")(grant_approval)
approvals_app.command("reject")(reject_approval)
app.add_typer(approvals_app, name="approvals")


if __name__ == "__main__":
    app()
