"""
Codebase Analyzer Sub-Agent
=============================

Explores and understands codebase structure, identifies relevant files,
and analyzes patterns and architecture.
"""

import os
from pathlib import Path
from typing import Any

from .base import SubAgent, SubAgentResult


class CodebaseAnalyzer(SubAgent):
    """Sub-agent for codebase exploration and analysis.

    Analyzes codebase to identify:
    - Project structure and organization
    - Relevant files for a feature
    - Existing patterns and conventions
    - Technology stack and dependencies
    - Entry points and key modules

    Input data structure:
    {
        "task": str,                    # Task description
        "search_terms": List[str] (optional),  # Keywords to search
        "file_patterns": List[str] (optional), # File glob patterns
        "max_depth": int (optional, default=5) # Max directory depth
    }

    Output data structure:
    {
        "project_structure": Dict,      # Directory tree summary
        "relevant_files": List[str],    # Files relevant to task
        "file_patterns": Dict,          # Detected patterns (naming, organization)
        "tech_stack": List[str],        # Technologies detected
        "entry_points": List[str],      # Main entry points
        "conventions": List[str]        # Coding conventions detected
    }
    """

    @property
    def name(self) -> str:
        return "Codebase Analyzer"

    @property
    def description(self) -> str:
        return "Explores codebase structure and identifies relevant files"

    def analyze(self, input_data: dict[str, Any]) -> SubAgentResult:
        """Analyze codebase structure and find relevant files.

        Args:
            input_data: Dictionary with 'task' and optional search parameters

        Returns:
            SubAgentResult with codebase analysis
        """
        task = input_data.get("task", "")
        search_terms = input_data.get("search_terms", [])
        file_patterns = input_data.get("file_patterns", [])
        max_depth = input_data.get("max_depth", 5)

        if not self.project_dir.exists():
            return SubAgentResult(
                success=False,
                data={},
                reasoning=f"Project directory does not exist: {self.project_dir}",
                confidence=0.0,
            )

        # Analyze project structure
        structure = self._analyze_structure(max_depth)

        # Detect technology stack
        tech_stack = self._detect_tech_stack()

        # Find relevant files based on task
        relevant_files = self._find_relevant_files(
            task, search_terms, file_patterns, max_depth
        )

        # Identify entry points
        entry_points = self._identify_entry_points()

        # Detect file patterns and conventions
        file_pattern_analysis = self._analyze_file_patterns()

        # Detect coding conventions
        conventions = self._detect_conventions()

        # Calculate confidence based on findings
        confidence = 0.5  # Base confidence
        if relevant_files:
            confidence += 0.3
        if tech_stack:
            confidence += 0.1
        if entry_points:
            confidence += 0.1

        confidence = min(1.0, confidence)

        # Generate recommendations
        recommendations = []
        if not relevant_files:
            recommendations.append(
                "No relevant files found - consider creating new files"
            )
        elif len(relevant_files) > 10:
            recommendations.append(
                f"Found {len(relevant_files)} relevant files - prioritize core modules"
            )

        if not entry_points:
            recommendations.append("No clear entry points found - review project structure")

        return SubAgentResult(
            success=True,
            data={
                "project_structure": structure,
                "relevant_files": relevant_files,
                "file_patterns": file_pattern_analysis,
                "tech_stack": tech_stack,
                "entry_points": entry_points,
                "conventions": conventions,
            },
            reasoning=self._generate_reasoning(
                structure, tech_stack, relevant_files, entry_points
            ),
            confidence=confidence,
            recommendations=recommendations,
            metadata={
                "project_dir": str(self.project_dir),
                "max_depth": max_depth,
                "total_files_scanned": structure.get("total_files", 0),
            },
        )

    def _analyze_structure(self, max_depth: int) -> dict[str, Any]:
        """Analyze project directory structure.

        Returns summary of directory tree and file counts.
        """
        structure = {
            "root": str(self.project_dir.name),
            "directories": [],
            "total_files": 0,
            "total_dirs": 0,
        }

        try:
            for root, dirs, files in os.walk(self.project_dir):
                # Skip hidden directories and common non-source directories
                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".")
                    and d not in ["node_modules", "__pycache__", "dist", "build", ".git"]
                ]

                depth = len(Path(root).relative_to(self.project_dir).parts)
                if depth > max_depth:
                    continue

                if root != str(self.project_dir):
                    rel_path = str(Path(root).relative_to(self.project_dir))
                    structure["directories"].append(rel_path)
                    structure["total_dirs"] += 1

                structure["total_files"] += len(files)

        except Exception as e:
            structure["error"] = str(e)

        return structure

    def _detect_tech_stack(self) -> list[str]:
        """Detect technology stack from project files.

        Looks for indicators like package.json, requirements.txt, etc.
        """
        tech_stack = []

        # Check for common technology indicators
        indicators = {
            "Node.js/JavaScript": ["package.json", "yarn.lock", "pnpm-lock.yaml"],
            "Python": ["requirements.txt", "setup.py", "pyproject.toml", "Pipfile"],
            "TypeScript": ["tsconfig.json"],
            "React": ["package.json"],  # Additional check needed
            "Vue": ["vue.config.js", "vite.config.js"],
            "Django": ["manage.py", "settings.py"],
            "Flask": ["app.py", "application.py"],
            "FastAPI": ["main.py"],  # Additional check needed
            "Docker": ["Dockerfile", "docker-compose.yml"],
            "Git": [".git"],
        }

        for tech, files in indicators.items():
            for file_name in files:
                if (self.project_dir / file_name).exists():
                    if tech not in tech_stack:
                        tech_stack.append(tech)
                    break

        # Check package.json for React
        package_json = self.project_dir / "package.json"
        if package_json.exists():
            try:
                import json

                with open(package_json) as f:
                    pkg = json.load(f)
                    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    if "react" in deps and "React" not in tech_stack:
                        tech_stack.append("React")
                    if "next" in deps:
                        tech_stack.append("Next.js")
                    if "vue" in deps and "Vue" not in tech_stack:
                        tech_stack.append("Vue")
            except Exception:
                pass

        return tech_stack

    def _find_relevant_files(
        self,
        task: str,
        search_terms: list[str],
        file_patterns: list[str],
        max_depth: int,
    ) -> list[str]:
        """Find files relevant to the task.

        Uses task description and search terms to identify relevant files.
        """
        relevant = []
        task_lower = task.lower() if task else ""

        # Extract keywords from task
        keywords = set(search_terms)
        if task:
            # Simple keyword extraction (split on non-alphanumeric)
            import re

            words = re.findall(r"\w+", task_lower)
            keywords.update(
                w for w in words if len(w) > 3 and w not in ["that", "with", "this"]
            )

        try:
            for root, dirs, files in os.walk(self.project_dir):
                # Skip hidden and common non-source directories
                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".")
                    and d not in ["node_modules", "__pycache__", "dist", "build", ".git"]
                ]

                depth = len(Path(root).relative_to(self.project_dir).parts)
                if depth > max_depth:
                    continue

                for file in files:
                    # Skip non-source files
                    if not self._is_source_file(file):
                        continue

                    file_path = Path(root) / file
                    rel_path = str(file_path.relative_to(self.project_dir))

                    # Check file patterns
                    if file_patterns:
                        if any(
                            pattern in rel_path.lower() for pattern in file_patterns
                        ):
                            relevant.append(rel_path)
                            continue

                    # Check if filename matches keywords
                    file_lower = file.lower()
                    if any(kw in file_lower for kw in keywords):
                        relevant.append(rel_path)
                        continue

                    # Check directory path
                    if any(kw in root.lower() for kw in keywords):
                        relevant.append(rel_path)

        except Exception:
            pass

        return relevant[:50]  # Limit to top 50 files

    def _identify_entry_points(self) -> list[str]:
        """Identify main entry points of the application."""
        entry_points = []

        # Common entry point patterns
        entry_files = [
            "main.py",
            "app.py",
            "server.py",
            "index.js",
            "index.ts",
            "main.js",
            "main.ts",
            "index.html",
            "App.tsx",
            "App.jsx",
        ]

        for file_name in entry_files:
            for file_path in self.project_dir.rglob(file_name):
                # Skip node_modules and similar
                if any(
                    part in file_path.parts
                    for part in ["node_modules", "dist", "build", "__pycache__"]
                ):
                    continue

                rel_path = str(file_path.relative_to(self.project_dir))
                entry_points.append(rel_path)

        return entry_points[:10]  # Limit to top 10

    def _analyze_file_patterns(self) -> dict[str, Any]:
        """Analyze file naming and organization patterns."""
        patterns = {
            "naming_convention": "unknown",
            "file_extensions": set(),
            "common_prefixes": [],
            "common_suffixes": [],
        }

        files_checked = 0
        max_files = 100

        try:
            for root, dirs, files in os.walk(self.project_dir):
                if files_checked >= max_files:
                    break

                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".")
                    and d not in ["node_modules", "__pycache__", "dist", "build"]
                ]

                for file in files:
                    if not self._is_source_file(file):
                        continue

                    files_checked += 1

                    # Collect extensions
                    ext = Path(file).suffix
                    if ext:
                        patterns["file_extensions"].add(ext)

                    # Detect naming convention (snake_case, camelCase, PascalCase, kebab-case)
                    if "_" in file:
                        patterns["naming_convention"] = "snake_case"
                    elif "-" in file:
                        patterns["naming_convention"] = "kebab-case"
                    elif file[0].isupper():
                        patterns["naming_convention"] = "PascalCase"

                    if files_checked >= max_files:
                        break

        except Exception:
            pass

        patterns["file_extensions"] = list(patterns["file_extensions"])
        return patterns

    def _detect_conventions(self) -> list[str]:
        """Detect coding conventions from codebase."""
        conventions = []

        # Check for linter configs
        if (self.project_dir / ".eslintrc.json").exists():
            conventions.append("ESLint configured")
        if (self.project_dir / ".pylintrc").exists():
            conventions.append("Pylint configured")
        if (self.project_dir / ".prettierrc").exists():
            conventions.append("Prettier configured")
        if (self.project_dir / "pyproject.toml").exists():
            conventions.append("Python project uses pyproject.toml")

        # Check for testing frameworks
        if (self.project_dir / "pytest.ini").exists():
            conventions.append("Uses pytest for testing")
        if (self.project_dir / "jest.config.js").exists():
            conventions.append("Uses Jest for testing")

        return conventions

    def _is_source_file(self, filename: str) -> bool:
        """Check if file is a source code file."""
        source_extensions = {
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".java",
            ".cpp",
            ".c",
            ".go",
            ".rs",
            ".rb",
            ".php",
            ".swift",
            ".kt",
            ".scala",
            ".sh",
            ".sql",
            ".html",
            ".css",
            ".scss",
            ".sass",
            ".vue",
        }

        return Path(filename).suffix in source_extensions

    def _generate_reasoning(
        self,
        structure: dict,
        tech_stack: list[str],
        relevant_files: list[str],
        entry_points: list[str],
    ) -> str:
        """Generate reasoning explanation for the analysis."""
        parts = []

        parts.append(
            f"Analyzed project with {structure.get('total_files', 0)} files "
            f"across {structure.get('total_dirs', 0)} directories"
        )

        if tech_stack:
            parts.append(f"Detected technologies: {', '.join(tech_stack)}")

        if relevant_files:
            parts.append(f"Found {len(relevant_files)} relevant files for this task")

        if entry_points:
            parts.append(f"Identified {len(entry_points)} entry points")

        return ". ".join(parts) + "."
