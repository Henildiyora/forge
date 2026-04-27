from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from forge.agents.librarian.agent import LibrarianAgent
from forge.cli.runtime import cli_settings, local_message_bus, run_async
from forge.conversation.engine import ConversationEngine
from forge.core.builds import (
    generate_strategy_artifacts,
    index_project,
    request_build_approval,
    validate_kubernetes_build,
    write_generated_artifacts,
)
from forge.core.checkpoints import CheckpointStore
from forge.core.llm import LLMClient
from forge.core.strategies import DeploymentStrategy
from forge.core.workspace import ConnectionProfile, ConversationSession, ForgeWorkspace


def build(
    project_path: Annotated[
        Path | None,
        typer.Argument(exists=True, file_okay=False, dir_okay=True),
    ] = None,
    goal: Annotated[
        str | None,
        typer.Option("--goal", help="Free-form description of what you want to deploy."),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", help="Where FORGE should write generated artifacts."),
    ] = None,
    auto_approve: Annotated[
        bool,
        typer.Option("--auto-approve", help="Approve the recommended strategy without a prompt."),
    ] = False,
    live: Annotated[
        bool,
        typer.Option(
            "--live",
            help="Prepare this build for live Kubernetes deployment after approval.",
        ),
    ] = False,
) -> None:
    """Run the FORGE conversation flow, generate artifacts, and validate when possible."""

    resolved_project_path = project_path or Path.cwd()
    settings = cli_settings()
    bus = local_message_bus(settings)
    workspace = ForgeWorkspace(resolved_project_path, settings)
    connection = workspace.load_connection() or ConnectionProfile(
        llm_backend=settings.llm_backend,
        llm_model=settings.llm_model,
        approval_transport="web",
    )
    effective_settings = settings.model_copy(
        update={"llm_backend": connection.llm_backend, "llm_model": connection.llm_model}
    )
    librarian = LibrarianAgent(settings=effective_settings, message_bus=bus)
    scan_result = workspace.load_index()
    if scan_result is None:
        scan_result = run_async(
            index_project(
                project_path=resolved_project_path,
                settings=effective_settings,
                workspace=workspace,
                librarian=librarian,
            )
        )

    build_goal = goal or typer.prompt("What are you trying to deploy?")
    llm = LLMClient(effective_settings)
    engine = ConversationEngine(llm=llm, scan_result=scan_result)
    intent = run_async(engine.interpret_intent(build_goal))

    while engine.needs_clarification(intent):
        question = run_async(engine.next_clarification_question(intent))
        typer.echo(question.render_terminal_box())
        answer = typer.prompt("")
        normalized = answer
        if answer.isdigit():
            index_value = int(answer) - 1
            if 0 <= index_value < len(question.options):
                normalized = question.options[index_value].value
        engine.record_answer(question, normalized)
        if question.question_key == "cloud_provider" and normalized in {"aws", "gcp", "azure"}:
            intent.mentioned_cloud = normalized
        if question.question_key == "service_count" and normalized.isdigit():
            intent.mentioned_scale = "medium" if int(normalized) > 1 else "small"

    selection = engine.select_strategy(intent)
    decision = run_async(engine.build_recommendation(selection.strategy, build_goal))
    typer.echo(f"FORGE recommends: {decision.strategy.value}")
    typer.echo(decision.reasoning)
    typer.echo(f"Estimated setup time: {decision.estimated_setup_time}")
    if decision.requirements:
        typer.echo(f"Requirements: {decision.requirements}")

    approved = auto_approve or typer.confirm("Approve this strategy?", default=True)
    if not approved:
        alternatives = list(DeploymentStrategy)
        typer.echo("Available strategies:")
        for index_value, strategy in enumerate(alternatives, start=1):
            typer.echo(f"  [{index_value}] {strategy.value}")
        chosen = typer.prompt("Choose a strategy number", default="1")
        choice_index = max(1, min(len(alternatives), int(chosen))) - 1
        decision.strategy = alternatives[choice_index]
    decision.user_confirmed = True

    session = ConversationSession(
        task_id=f"build-session-{resolved_project_path.name}",
        goal=build_goal,
        strategy=decision.strategy.value,
        questions_asked=engine.questions_asked,
        decision_payload=decision.model_dump(mode="json"),
    )
    workspace.save_session(session)

    generated = run_async(
        generate_strategy_artifacts(
            settings=effective_settings,
            project_path=resolved_project_path,
            strategy=decision.strategy,
            cloud=connection.cloud_provider or intent.mentioned_cloud,
            message_bus=bus,
        )
    )
    artifact_dir = output_dir or (workspace.workspace_dir / "generated")
    written = write_generated_artifacts(
        output_dir=artifact_dir,
        generated=generated,
        workspace=workspace,
    )
    typer.echo(f"Wrote {len(written)} artifact(s) to: {Path(artifact_dir).resolve()}")

    sandbox_validation = run_async(
        validate_kubernetes_build(
            settings=effective_settings,
            project_path=resolved_project_path,
            generated=generated,
            message_bus=bus,
        )
    )
    if sandbox_validation is not None:
        typer.echo(
            f"Sandbox validation: {'passed' if sandbox_validation.smoke_test.passed else 'failed'}"
        )
        if live and sandbox_validation.smoke_test.passed:
            checkpoint_store = CheckpointStore(effective_settings)
            approval_url = (
                f"{effective_settings.approval_base_url}/api/v1/approvals/"
                f"{generated.task_id}"
            )
            request = run_async(
                request_build_approval(
                    generated=generated,
                    checkpoint_store=checkpoint_store,
                    approval_summary=(
                        f"FORGE build approval for {resolved_project_path.name}"
                    ),
                    approval_url=approval_url,
                )
            )
            typer.echo(f"Approval request id: {request.id}")
            typer.echo(f"Review URL: {approval_url}")
