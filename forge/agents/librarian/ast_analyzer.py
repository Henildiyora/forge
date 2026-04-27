from __future__ import annotations

import ast
import json
import re
import tomllib
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ProjectLanguage = Literal["python", "node", "go", "java", "rust", "unknown"]
ProjectFramework = Literal[
    "fastapi",
    "express",
    "django",
    "flask",
    "spring",
    "gin",
    "standard-library",
    "unknown",
]
ChangeType = Literal["logic", "comment_or_style"]

SKIP_DIRECTORIES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}

ENV_PATTERNS: dict[ProjectLanguage, tuple[re.Pattern[str], ...]] = {
    "python": (
        re.compile(r'os\.getenv\(\s*["\']([A-Z][A-Z0-9_]*)["\']'),
        re.compile(r'os\.environ(?:\.get)?\(\s*["\']([A-Z][A-Z0-9_]*)["\']'),
        re.compile(r'os\.environ\[\s*["\']([A-Z][A-Z0-9_]*)["\']\s*\]'),
    ),
    "node": (
        re.compile(r"process\.env\.([A-Z][A-Z0-9_]*)"),
        re.compile(r'process\.env\[\s*["\']([A-Z][A-Z0-9_]*)["\']\s*\]'),
    ),
    "go": (
        re.compile(r'os\.Getenv\(\s*"([A-Z][A-Z0-9_]*)"\s*\)'),
        re.compile(r'LookupEnv\(\s*"([A-Z][A-Z0-9_]*)"\s*\)'),
    ),
    "java": (),
    "rust": (),
    "unknown": (),
}

PYTHON_PORT_PATTERN = re.compile(r"port\s*=\s*(\d{2,5})")
NODE_PORT_PATTERN = re.compile(
    r"(?:process\.env\.PORT\s*\|\|\s*|listen\(\s*)(\d{2,5})"
)
GO_PORT_PATTERN = re.compile(r'ListenAndServe\(\s*"[:]?(\d{2,5})"')


class CodebaseScanResult(BaseModel):
    """Structured output produced by the Librarian scanner."""

    project_path: str = Field(description="Filesystem path that was scanned.")
    language: ProjectLanguage = Field(
        description="Detected primary language for the repository."
    )
    framework: ProjectFramework = Field(
        description="Detected primary framework for the repository."
    )
    entry_point: str = Field(description="Relative path of the inferred application entry point.")
    port: int | None = Field(
        default=None,
        description="Detected application port if one is explicitly defined.",
    )
    env_vars: list[str] = Field(
        default_factory=list,
        description="Environment variables referenced by runtime code.",
    )
    database_connections: list[str] = Field(
        default_factory=list,
        description="Detected backing services such as postgres, mongo, or redis.",
    )
    service_count: int = Field(
        default=1,
        ge=1,
        description="Estimated number of independently deployable services.",
    )
    detected_infra: list[str] = Field(
        default_factory=list,
        description="Existing deployment infrastructure detected in the repository.",
    )
    has_existing_infra: bool = Field(
        default=False,
        description="Whether repository deployment infrastructure already exists.",
    )
    file_count: int = Field(description="Number of source and config files examined.")
    evidence: list[str] = Field(
        default_factory=list,
        description="Human-readable evidence supporting the scan result.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence score for the scan result.",
    )


class DiffAnalysisResult(BaseModel):
    """Classification for a source-code change."""

    change_type: ChangeType = Field(
        description="Whether the change affects logic or only comments/style."
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence describing why the classification was chosen.",
    )


class ASTAnalyzer:
    """Analyze codebases and source changes for the Librarian agent."""

    def analyze_project(self, project_path: str | Path) -> CodebaseScanResult:
        """Scan a repository and summarize its runtime characteristics."""

        root = Path(project_path).expanduser().resolve()
        files = self._collect_files(root)
        language = self._detect_language(files)
        framework = self._detect_framework(root, files, language)
        entry_point = self._detect_entry_point(root, files, language)
        port = self._detect_port(root, entry_point, files, language)
        env_vars = self._extract_env_vars(root, files, language)
        database_connections = self._detect_database_connections(root, files, env_vars)
        service_count = self._estimate_service_count(root, files, language)
        detected_infra = self._detect_existing_infra(files)
        evidence = self._build_evidence(
            root=root,
            files=files,
            language=language,
            framework=framework,
            entry_point=entry_point,
            port=port,
            env_vars=env_vars,
            database_connections=database_connections,
            service_count=service_count,
            detected_infra=detected_infra,
        )
        confidence = self._score_confidence(language, framework, entry_point, port)
        return CodebaseScanResult(
            project_path=str(root),
            language=language,
            framework=framework,
            entry_point=entry_point,
            port=port,
            env_vars=env_vars,
            database_connections=database_connections,
            service_count=service_count,
            detected_infra=detected_infra,
            has_existing_infra=bool(detected_infra),
            file_count=len(files),
            evidence=evidence,
            confidence=confidence,
        )

    def classify_source_change(
        self,
        before: str,
        after: str,
        *,
        file_path: str,
    ) -> DiffAnalysisResult:
        """Classify a source change as logic or comment/style only."""

        language = self._language_from_path(Path(file_path))
        if language == "python":
            return self._classify_python_change(before, after)
        return self._classify_text_change(before, after, language=language)

    def _collect_files(self, root: Path) -> list[Path]:
        files: list[Path] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRECTORIES for part in path.parts):
                continue
            files.append(path)
        return sorted(files)

    def _detect_language(self, files: list[Path]) -> ProjectLanguage:
        scores: Counter[ProjectLanguage] = Counter()
        for path in files:
            if path.name == "pyproject.toml" or path.suffix == ".py":
                scores["python"] += 2
            if path.name == "package.json" or path.suffix in {".js", ".ts"}:
                scores["node"] += 2
            if path.name == "go.mod" or path.suffix == ".go":
                scores["go"] += 2
            if path.name == "pom.xml" or path.suffix == ".java":
                scores["java"] += 2
            if path.name == "Cargo.toml" or path.suffix == ".rs":
                scores["rust"] += 2
        if not scores:
            return "unknown"
        return scores.most_common(1)[0][0]

    def _detect_framework(
        self,
        root: Path,
        files: list[Path],
        language: ProjectLanguage,
    ) -> ProjectFramework:
        if language == "python":
            dependencies = self._python_dependencies(root)
            if "fastapi" in dependencies:
                return "fastapi"
            if "django" in dependencies:
                return "django"
            if "flask" in dependencies:
                return "flask"
        if language == "node":
            dependencies = self._node_dependencies(root)
            if "express" in dependencies:
                return "express"
        if language == "go":
            joined = "\n".join(self._read_text(path) for path in files if path.suffix == ".go")
            if "github.com/gin-gonic/gin" in joined:
                return "gin"
            if "net/http" in joined:
                return "standard-library"
        return "unknown"

    def _detect_entry_point(
        self,
        root: Path,
        files: list[Path],
        language: ProjectLanguage,
    ) -> str:
        candidates: dict[ProjectLanguage, tuple[str, ...]] = {
            "python": ("main.py", "app/main.py", "src/main.py"),
            "node": ("index.js", "server.js", "app.js", "src/index.js"),
            "go": ("main.go", "cmd/main.go", "cmd/server/main.go"),
            "java": ("src/main/java/Main.java",),
            "rust": ("src/main.rs",),
            "unknown": (),
        }
        relative_paths = {path.relative_to(root).as_posix(): path for path in files}
        for candidate in candidates[language]:
            if candidate in relative_paths:
                return candidate
        for relative_path in relative_paths:
            if relative_path.endswith("/main.py") or relative_path.endswith("/main.go"):
                return relative_path
        if relative_paths:
            return sorted(relative_paths)[0]
        return ""

    def _detect_port(
        self,
        root: Path,
        entry_point: str,
        files: list[Path],
        language: ProjectLanguage,
    ) -> int | None:
        candidate_paths: list[Path] = []
        if entry_point:
            candidate_paths.append(root / entry_point)
        candidate_paths.extend(files)
        counters: Counter[int] = Counter()
        for path in candidate_paths:
            if not path.exists():
                continue
            content = self._read_text(path)
            pattern = self._port_pattern(language)
            for match in pattern.findall(content):
                counters[int(match)] += 1
        if not counters:
            return None
        return counters.most_common(1)[0][0]

    def _extract_env_vars(
        self,
        root: Path,
        files: list[Path],
        language: ProjectLanguage,
    ) -> list[str]:
        env_vars: set[str] = set()
        patterns = ENV_PATTERNS[language]
        for path in files:
            if not self._is_relevant_source_file(path, language):
                continue
            content = self._read_text(path)
            for pattern in patterns:
                env_vars.update(pattern.findall(content))
        return sorted(env_vars)

    def _detect_database_connections(
        self,
        root: Path,
        files: list[Path],
        env_vars: list[str],
    ) -> list[str]:
        detected: set[str] = set()
        joined = "\n".join(self._read_text(path) for path in files[:200])
        uppercase_env = {name.upper() for name in env_vars}
        if "DATABASE_URL" in uppercase_env or "postgres" in joined.lower():
            detected.add("postgres")
        if "MONGO_URL" in uppercase_env or "mongodb" in joined.lower():
            detected.add("mongo")
        if "REDIS_URL" in uppercase_env or "redis://" in joined.lower():
            detected.add("redis")
        if "MYSQL_URL" in uppercase_env or "mysql" in joined.lower():
            detected.add("mysql")
        return sorted(detected)

    def _estimate_service_count(
        self,
        root: Path,
        files: list[Path],
        language: ProjectLanguage,
    ) -> int:
        if language == "node":
            nested_packages = [
                path for path in files if path.name == "package.json" and path.parent != root
            ]
            if nested_packages:
                return max(1, len(nested_packages))
        if language == "python":
            nested_entries = {
                path.parent.relative_to(root).as_posix()
                for path in files
                if path.name in {"main.py", "app.py"} and path.parent != root
            }
            if nested_entries:
                return max(1, len(nested_entries))
        return 1

    def _detect_existing_infra(self, files: list[Path]) -> list[str]:
        detected: set[str] = set()
        for path in files:
            lower_name = path.name.lower()
            lower_path = path.as_posix().lower()
            if lower_name in {"docker-compose.yml", "docker-compose.yaml"}:
                detected.add("docker_compose")
            if lower_name == "dockerfile":
                detected.add("docker")
            if lower_name == "serverless.yml":
                detected.add("serverless")
            if lower_name.endswith(".tf") or "terraform" in lower_path:
                detected.add("terraform")
            if ".github/workflows" in lower_path or "gitlab-ci" in lower_name:
                detected.add("cicd")
            if (
                lower_name.endswith((".yaml", ".yml"))
                and (
                    "k8s" in lower_path
                    or "kubernetes" in lower_path
                    or "deployment" in lower_name
                )
            ):
                detected.add("kubernetes")
        return sorted(detected)

    def _build_evidence(
        self,
        *,
        root: Path,
        files: list[Path],
        language: ProjectLanguage,
        framework: ProjectFramework,
        entry_point: str,
        port: int | None,
        env_vars: list[str],
        database_connections: list[str],
        service_count: int,
        detected_infra: list[str],
    ) -> list[str]:
        evidence = [f"Scanned {len(files)} files in {root.name}."]
        if language != "unknown":
            evidence.append(f"Detected language {language}.")
        if framework != "unknown":
            evidence.append(f"Detected framework {framework}.")
        if entry_point:
            evidence.append(f"Detected entry point {entry_point}.")
        if port is not None:
            evidence.append(f"Detected port {port}.")
        evidence.append(f"Estimated service count {service_count}.")
        if env_vars:
            evidence.append(f"Found env vars: {', '.join(env_vars)}.")
        if database_connections:
            evidence.append(
                f"Detected data services: {', '.join(database_connections)}."
            )
        if detected_infra:
            evidence.append(
                f"Detected existing infra: {', '.join(detected_infra)}."
            )
        return evidence

    def _score_confidence(
        self,
        language: ProjectLanguage,
        framework: ProjectFramework,
        entry_point: str,
        port: int | None,
    ) -> float:
        score = 0.2
        if language != "unknown":
            score += 0.3
        if framework != "unknown":
            score += 0.2
        if entry_point:
            score += 0.2
        if port is not None:
            score += 0.1
        return min(score, 0.99)

    def _python_dependencies(self, root: Path) -> set[str]:
        dependencies: set[str] = set()
        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            content = tomllib.loads(self._read_text(pyproject))
            project_table = content.get("project")
            if isinstance(project_table, dict):
                raw_dependencies = project_table.get("dependencies", [])
                if isinstance(raw_dependencies, list):
                    dependencies.update(self._clean_dependency_names(raw_dependencies))
        requirements = root / "requirements.txt"
        if requirements.exists():
            raw_dependencies = requirements.read_text(encoding="utf-8").splitlines()
            dependencies.update(self._clean_dependency_names(raw_dependencies))
        return dependencies

    def _node_dependencies(self, root: Path) -> set[str]:
        package_json = root / "package.json"
        if not package_json.exists():
            return set()
        content = json.loads(self._read_text(package_json))
        dependencies: set[str] = set()
        for key in ("dependencies", "devDependencies"):
            raw_dependencies = content.get(key, {})
            if isinstance(raw_dependencies, dict):
                dependencies.update(str(name).lower() for name in raw_dependencies)
        return dependencies

    def _clean_dependency_names(self, dependencies: list[str]) -> set[str]:
        cleaned: set[str] = set()
        for dependency in dependencies:
            normalized = dependency.strip().split("[", 1)[0]
            normalized = re.split(r"[<>=!~ ]", normalized, maxsplit=1)[0]
            if normalized:
                cleaned.add(normalized.lower())
        return cleaned

    def _classify_python_change(self, before: str, after: str) -> DiffAnalysisResult:
        before_ast = self._normalized_python_ast(before)
        after_ast = self._normalized_python_ast(after)
        if before_ast == after_ast:
            return DiffAnalysisResult(
                change_type="comment_or_style",
                evidence=["Python AST is unchanged after removing docstrings and comments."],
            )
        return DiffAnalysisResult(
            change_type="logic",
            evidence=[
                "Python AST changed after normalization, indicating executable "
                "logic changed."
            ],
        )

    def _classify_text_change(
        self,
        before: str,
        after: str,
        *,
        language: ProjectLanguage,
    ) -> DiffAnalysisResult:
        normalized_before = self._normalize_non_python_source(before, language)
        normalized_after = self._normalize_non_python_source(after, language)
        if normalized_before == normalized_after:
            return DiffAnalysisResult(
                change_type="comment_or_style",
                evidence=["Normalized source without comments and whitespace is unchanged."],
            )
        return DiffAnalysisResult(
            change_type="logic",
            evidence=["Normalized source changed, indicating executable content changed."],
        )

    def _normalized_python_ast(self, source: str) -> str:
        tree = ast.parse(source)
        stripped = self._strip_python_docstrings(tree)
        return ast.dump(stripped, annotate_fields=True, include_attributes=False)

    def _strip_python_docstrings(self, node: ast.AST) -> ast.AST:
        for child in ast.iter_child_nodes(node):
            self._strip_python_docstrings(child)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = list(node.body)
            if body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    node.body = body[1:]
        return node

    def _normalize_non_python_source(
        self,
        source: str,
        language: ProjectLanguage,
    ) -> str:
        without_line_comments = source
        if language == "node":
            without_line_comments = re.sub(r"//.*", "", without_line_comments)
        if language == "go":
            without_line_comments = re.sub(r"//.*", "", without_line_comments)
        without_block_comments = re.sub(r"/\*.*?\*/", "", without_line_comments, flags=re.S)
        without_hash_comments = re.sub(r"^\s*#.*$", "", without_block_comments, flags=re.M)
        return re.sub(r"\s+", "", without_hash_comments)

    def _language_from_path(self, path: Path) -> ProjectLanguage:
        if path.suffix == ".py":
            return "python"
        if path.suffix in {".js", ".ts"}:
            return "node"
        if path.suffix == ".go":
            return "go"
        if path.suffix == ".java":
            return "java"
        if path.suffix == ".rs":
            return "rust"
        return "unknown"

    def _is_relevant_source_file(self, path: Path, language: ProjectLanguage) -> bool:
        if language == "python":
            return path.suffix == ".py"
        if language == "node":
            return path.suffix in {".js", ".ts", ".json"}
        if language == "go":
            return path.suffix == ".go"
        return True

    def _port_pattern(self, language: ProjectLanguage) -> re.Pattern[str]:
        if language == "python":
            return PYTHON_PORT_PATTERN
        if language == "node":
            return NODE_PORT_PATTERN
        if language == "go":
            return GO_PORT_PATTERN
        return re.compile(r"$^")

    def _read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="ignore")
