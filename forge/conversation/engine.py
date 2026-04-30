from __future__ import annotations

from pydantic import BaseModel, Field

from forge.agents.librarian.ast_analyzer import CodebaseScanResult
from forge.conversation.prompts import clarification_prompt, intent_prompt, recommendation_prompt
from forge.conversation.questions import ClarificationOption, ClarificationQuestion
from forge.conversation.strategy_selector import (
    StrategySelectionContext,
    StrategySelectionResult,
    UserIntentLike,
    select_strategy,
)
from forge.core.llm import LLMClient
from forge.core.strategies import DeploymentStrategy


class UserIntent(BaseModel):
    """Structured output from NLP interpretation of user deployment input."""

    wants_simplicity: bool = Field(description="Whether the user prefers a simpler setup.")
    has_existing_infra: bool = Field(
        description="Whether the user has mentioned existing infrastructure."
    )
    mentioned_scale: str | None = Field(
        default=None,
        description='Mentioned scale: "small", "medium", "large", or null.',
    )
    mentioned_cloud: str | None = Field(
        default=None,
        description='Mentioned cloud: "aws", "gcp", "azure", or null.',
    )
    mentioned_tools: list[str] = Field(
        default_factory=list,
        description="Deployment tools or platforms explicitly mentioned by the user.",
    )
    is_greenfield: bool = Field(description="True when the project is net-new.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in the extracted intent.")


class DeploymentDecision(BaseModel):
    """Final structured deployment decision produced by the FORGE conversation layer."""

    strategy: DeploymentStrategy = Field(description="Chosen deployment strategy.")
    reasoning: str = Field(description="Human-readable explanation shown to the user.")
    requirements: list[str] = Field(
        default_factory=list,
        description="What the user needs available before generation or deployment.",
    )
    what_will_be_generated: list[str] = Field(
        default_factory=list,
        description="Exact classes of files or artifacts FORGE will generate.",
    )
    estimated_setup_time: str = Field(description="Estimated setup time string.")
    user_confirmed: bool = Field(
        default=False,
        description="Set to true only after explicit user approval.",
    )


class ConversationEngine:
    """Structured conversation manager that turns user intent into a deployment decision."""

    MAX_QUESTIONS = 3

    def __init__(self, llm: LLMClient, scan_result: CodebaseScanResult) -> None:
        self.llm = llm
        self.scan = scan_result
        self.intent: UserIntent | None = None
        self.questions_asked = 0
        self.context = StrategySelectionContext()
        self._docker_goal_detected = False

    async def interpret_intent(self, user_input: str) -> UserIntent:
        """Use the configured LLM backend to extract structured intent."""

        response = await self.llm.complete(
            prompt=intent_prompt(self.scan, user_input),
            task_id=f"intent-{self.scan.project_path}",
            agent="conversation_engine",
            expected_format="json",
        )
        self.intent = UserIntent.model_validate(response.data)
        normalized_goal = user_input.lower()
        self._docker_goal_detected = any(
            marker in normalized_goal
            for marker in ("docker", "dockerhub", "containerize", "container")
        )
        self.context.wants_cicd_only = any(
            marker in user_input.lower()
            for marker in ("pipeline only", "ci/cd only", "just ci", "just pipeline")
        )
        self.context.wants_local_only = any(
            marker in user_input.lower() for marker in ("local", "one machine", "docker compose")
        )
        if self.intent.mentioned_cloud is not None:
            self.context.preferred_cloud = self.intent.mentioned_cloud
        return self.intent

    def needs_clarification(self, intent: UserIntent) -> bool:
        """Return whether FORGE should ask one more clarification question."""

        if self.questions_asked >= self.MAX_QUESTIONS:
            return False
        if self._needs_strategy_conflict_question(intent):
            return True
        if intent.confidence < 0.75:
            return True
        if intent.mentioned_cloud is None and "serverless" in intent.mentioned_tools:
            return True
        if self.scan.service_count <= 1 and intent.mentioned_scale is None:
            return True
        return False

    async def next_clarification_question(self, intent: UserIntent) -> ClarificationQuestion:
        """Return the most important remaining clarification question."""

        if self._needs_strategy_conflict_question(intent):
            question = ClarificationQuestion(
                question_key="deployment_strategy_preference",
                prompt=(
                    "This project looks complex (multi-service), but your goal mentions Docker. "
                    "Which strategy do you want FORGE to use?"
                ),
                options=[
                    ClarificationOption(
                        key="docker_compose",
                        label="Docker Compose (simple, Docker/Docker Hub focused)",
                        value="docker_compose",
                    ),
                    ClarificationOption(
                        key="kubernetes",
                        label="Kubernetes (scalable, cluster-focused)",
                        value="kubernetes",
                    ),
                ],
                rationale=(
                    "Multi-service projects default toward Kubernetes, but FORGE should honor "
                    "explicit Docker-first intent when that is your goal."
                ),
            )
        elif intent.mentioned_cloud is None and "serverless" in intent.mentioned_tools:
            question = ClarificationQuestion(
                question_key="cloud_provider",
                prompt="Which cloud should FORGE target for this serverless deployment?",
                options=[
                    ClarificationOption(key="aws", label="AWS Lambda", value="aws"),
                    ClarificationOption(key="gcp", label="Google Cloud Run", value="gcp"),
                    ClarificationOption(
                        key="azure",
                        label="Azure (not recommended yet)",
                        value="azure",
                    ),
                    ClarificationOption(
                        key="unsure",
                        label="Not sure — let FORGE decide",
                        value="unknown",
                    ),
                ],
                rationale="FORGE needs a cloud target to choose the correct serverless generator.",
            )
        elif self.scan.service_count <= 1 and intent.mentioned_scale is None:
            question = ClarificationQuestion(
                question_key="service_count",
                prompt="How many separate services does this project have?",
                options=[
                    ClarificationOption(
                        key="one",
                        label="Just one (monolith or single API)",
                        value="1",
                    ),
                    ClarificationOption(
                        key="small",
                        label="2-5 services (small microservices)",
                        value="3",
                    ),
                    ClarificationOption(
                        key="large",
                        label="6+ services (full microservices platform)",
                        value="6",
                    ),
                    ClarificationOption(
                        key="unsure",
                        label="Not sure — let FORGE decide from the code",
                        value="unknown",
                    ),
                ],
                rationale=(
                    "Service count is a strong signal for choosing between compose "
                    "and Kubernetes."
                ),
            )
        else:
            response = await self.llm.complete(
                prompt=clarification_prompt(self.scan, "low confidence in user intent"),
                task_id=f"clarify-{self.scan.project_path}",
                agent="conversation_engine",
                expected_format="json",
            )
            question = ClarificationQuestion.model_validate(response.data)
        self.questions_asked += 1
        return question

    def record_answer(self, question: ClarificationQuestion, answer: str) -> None:
        """Record a clarification answer into deterministic strategy-selection context."""

        normalized = answer.strip().lower()
        if question.question_key == "service_count":
            if normalized.isdigit():
                self.context.service_count_hint = int(normalized)
            elif normalized in {"one", "single"}:
                self.context.service_count_hint = 1
            elif normalized in {"small", "2-5"}:
                self.context.service_count_hint = 3
            elif normalized in {"large", "6+"}:
                self.context.service_count_hint = 6
        if question.question_key == "cloud_provider":
            if normalized in {"aws", "gcp", "azure"}:
                self.context.preferred_cloud = normalized
        if question.question_key == "deployment_strategy_preference":
            if normalized == "docker_compose":
                self.context.forced_strategy = DeploymentStrategy.DOCKER_COMPOSE
            elif normalized == "kubernetes":
                self.context.forced_strategy = DeploymentStrategy.KUBERNETES

    def select_strategy(self, intent: UserIntent) -> StrategySelectionResult:
        """Select a deployment strategy with deterministic Python logic only."""

        intent_like = UserIntentLike.model_validate(intent.model_dump(mode="json"))
        if self.context.preferred_cloud is not None:
            intent_like.mentioned_cloud = self.context.preferred_cloud
        return select_strategy(self.scan, intent_like, self.context)

    def _needs_strategy_conflict_question(self, intent: UserIntent) -> bool:
        if self.context.forced_strategy is not None:
            return False
        mentions_kubernetes = any(
            tool.lower() in {"kubernetes", "k8s"} for tool in intent.mentioned_tools
        )
        if mentions_kubernetes:
            return False
        return self._docker_goal_detected and self.scan.service_count > 1

    async def build_recommendation(
        self,
        strategy: DeploymentStrategy,
        goal: str,
    ) -> DeploymentDecision:
        """Build the user-facing recommendation after deterministic strategy selection."""

        response = await self.llm.complete(
            prompt=recommendation_prompt(strategy, self.scan, goal),
            task_id=f"recommend-{self.scan.project_path}",
            agent="conversation_engine",
            expected_format="json",
        )
        payload = dict(response.data)
        payload["strategy"] = strategy.value
        return DeploymentDecision.model_validate(payload)
