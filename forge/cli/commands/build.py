from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from forge.agents.librarian.agent import LibrarianAgent
from forge.agents.manager.agent import ManagerAgent
from forge.agents.manager.orchestrator import run_manager_build_pipeline
from forge.cli.runtime import cli_settings, local_message_bus, run_async
from forge.conversation.engine import ConversationEngine
from forge.conversation.strategy_ranking import (
    ScoredStrategy,
    rank_strategies,
    resolve_strategy_choice,
)
from forge.conversation.strategy_selector import UserIntentLike
from forge.core import audit
from forge.core.builds import (
    generate_strategy_artifacts,
    generated_artifacts_from_swarm_state,
    index_project,
    request_build_approval,
    validate_kubernetes_build,
    write_generated_artifacts,
)
from forge.core.checkpoints import CheckpointStore
from forge.core.exceptions import SandboxToolingError
from forge.core.llm import LLMClient
from forge.core.strategies import DeploymentStrategy
from forge.core.workspace import ConnectionProfile, ConversationSession, ForgeWorkspace

_MANAGER_PIPELINED = frozenset(
    {
        DeploymentStrategy.DOCKER_COMPOSE,
        DeploymentStrategy.KUBERNETES,
        DeploymentStrategy.CICD_ONLY,
    }
)


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
    """Run the Manager-led build flow, generate artifacts, and validate when possible."""

    resolved_project_path = project_path or Path.cwd()
    settings = cli_settings()
    bus = local_message_bus(settings)
    workspace = ForgeWorkspace(resolved_project_path, settings)
    workspace.ensure()
    audit.configure_default_audit_log(workspace.workspace_dir / "audit.log")
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

    manager = ManagerAgent(settings=effective_settings, message_bus=bus)
    typer.echo(manager.format_project_preview(scan_result))
    if not auto_approve:
        if not typer.confirm("Does this project summary look correct?", default=True):
            typer.echo("Update the project or run `forge index` again, then rerun `forge build`.")
            raise typer.Exit(1)

    intent_like = UserIntentLike.model_validate(intent.model_dump(mode="json"))
    ranked = rank_strategies(
        scan_result,
        intent_like,
        engine.context,
        top_n=3,
        goal_lower=build_goal,
    )
    _print_ranked_strategies(ranked)

    if auto_approve:
        choice_raw = "1"
    else:
        choice_raw = typer.prompt(
            "Pick a strategy [1-3], or describe (e.g. docker compose, kubernetes, serverless)"
        )
    resolved = resolve_strategy_choice(choice_raw, ranked)
    chosen = resolved if resolved is not None else ranked[0]
    strategy = chosen.strategy

    decision = run_async(engine.build_recommendation(strategy, build_goal))
    decision.strategy = strategy
    typer.echo(f"FORGE recommends: {decision.strategy.value}")
    typer.echo(decision.reasoning)
    _print_strategy_quick_guide()
    typer.echo(f"Estimated setup time: {decision.estimated_setup_time}")
    if decision.requirements:
        typer.echo(f"Requirements: {decision.requirements}")

    approved = auto_approve or typer.confirm("Approve this strategy?", default=True)
    if not approved:
        alternatives = list(DeploymentStrategy)
        typer.echo("Available strategies:")
        for index_value, strat in enumerate(alternatives, start=1):
            typer.echo(f"  [{index_value}] {strat.value}")
        chosen_num = typer.prompt("Choose a strategy number", default="1")
        choice_index = max(1, min(len(alternatives), int(chosen_num))) - 1
        decision.strategy = alternatives[choice_index]
        strategy = decision.strategy

    decision.user_confirmed = True

    session = ConversationSession(
        task_id=f"build-session-{resolved_project_path.name}",
        goal=build_goal,
        strategy=decision.strategy.value,
        questions_asked=engine.questions_asked,
        decision_payload=decision.model_dump(mode="json"),
    )
    workspace.save_session(session)

    if decision.strategy in _MANAGER_PIPELINED:
        final_state = run_async(
            run_manager_build_pipeline(
                settings=effective_settings,
                message_bus=bus,
                project_path=resolved_project_path,
                scan=scan_result,
                strategy=decision.strategy,
            )
        )
        if final_state.current_step == "error":
            typer.secho("Captain review could not approve this build plan.", fg="red")
            for err in final_state.errors:
                typer.echo(f"  - {err}")
            raise typer.Exit(1)
        generated = generated_artifacts_from_swarm_state(
            task_id=final_state.task_id,
            strategy=decision.strategy,
            state=final_state,
        )
    else:
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
    _print_next_steps(artifact_dir=Path(artifact_dir), strategy=decision.strategy)
    audit.record(
        actor="forge_cli",
        action="artifact_written",
        target=str(Path(artifact_dir).resolve()),
        task_id=generated.task_id,
        evidence=generated.evidence[-3:],
        detail={"strategy": decision.strategy.value, "files": written},
    )

    try:
        sandbox_validation = run_async(
            validate_kubernetes_build(
                settings=effective_settings,
                project_path=resolved_project_path,
                generated=generated,
                message_bus=bus,
            )
        )
    except SandboxToolingError as exc:
        typer.secho("Kubernetes sandbox validation cannot run on this machine.", fg="yellow")
        typer.echo(str(exc))
        typer.echo("Recommended next steps:")
        typer.echo("  1) Install tooling: brew install loft-sh/tap/vcluster")
        typer.echo("  2) Or rerun `forge build` and choose Docker Compose for a simpler path")
        typer.echo("  3) Follow `.forge/generated/instruction_deploy.md` for exact commands")
        raise typer.Exit(code=1) from exc
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


def _print_ranked_strategies(ranked: list[ScoredStrategy]) -> None:
    typer.echo("\nTop deployment strategies (pick one):")
    for i, item in enumerate(ranked, start=1):
        typer.echo(f"\n  [{i}] {item.strategy.value} (score {item.score:.0f}/100)")
        typer.echo(f"      {item.reason}")
        typer.echo(f"      When: {item.when_to_use}")
        typer.echo(f"      Pros: {', '.join(item.pros)}")
        typer.echo(f"      Cons: {', '.join(item.cons)}")
        typer.echo(f"      Later: {item.migration_path}")
    typer.echo("")


def _print_next_steps(*, artifact_dir: Path, strategy: DeploymentStrategy) -> None:
    resolved = artifact_dir.expanduser().resolve()
    guide_path = resolved / "instruction_deploy.md"
    typer.echo("Next steps:")
    typer.echo(f"  1) Open the deployment guide: {guide_path}")
    typer.echo("  2) Review generated files and adjust image/env settings if needed")
    if strategy == DeploymentStrategy.DOCKER_COMPOSE:
        typer.echo("  3) Run: cd .forge/generated && docker compose -f docker-compose.generated.yml up --build")
    elif strategy == DeploymentStrategy.KUBERNETES:
        typer.echo("  3) Run: cd .forge/generated && kubectl apply -f deployment.yaml -f service.yaml")
    elif strategy == DeploymentStrategy.SERVERLESS:
        typer.echo("  3) Run: cd .forge/generated && serverless deploy")
    elif strategy == DeploymentStrategy.CICD_ONLY:
        typer.echo("  3) Copy `.github/workflows/generated-ci.yml` into your repo and commit it")
    else:
        typer.echo("  3) Review supplemental platform files in `.forge/generated`")
    typer.echo("  4) Ask the Manager: `forge ask \"why did you pick this strategy?\"`")
    typer.echo("  5) If anything is unclear, rerun `forge build --goal \"...\"` with more detail.")


def _print_strategy_quick_guide() -> None:
    typer.echo("Quick strategy guide:")
    typer.echo("  - Docker Compose: best for learning, single-machine deploys, and faster setup")
    typer.echo("  - Kubernetes: best for scaling, resilience, and multi-service operations")
    typer.echo("  - Start simple, then migrate later when traffic and complexity increase")
