from __future__ import annotations

import typer

from forge.cli.commands.approvals import grant_approval, list_approvals, reject_approval
from forge.cli.commands.build import build
from forge.cli.commands.connect import connect
from forge.cli.commands.index import index
from forge.cli.commands.monitor import monitor
from forge.cli.commands.status import status

app = typer.Typer(help="FORGE CLI")
approvals_app = typer.Typer(help="Approval request operations", hidden=True)
app.command("connect")(connect)
app.command("index")(index)
app.command("build")(build)
app.command("monitor")(monitor)
app.command("status")(status)
approvals_app.command("list")(list_approvals)
approvals_app.command("grant")(grant_approval)
approvals_app.command("reject")(reject_approval)
app.add_typer(approvals_app, name="approvals")


if __name__ == "__main__":
    app()
