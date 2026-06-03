# BMad Method Sub-Agents

Specialized analysis agents that can be invoked by main agents for focused tasks like requirements analysis, codebase exploration, and technical evaluation.

## Overview

Sub-agents follow BMad Method principles:
- **Single responsibility** - One focused task per sub-agent
- **Composable** - Can be chained together
- **Stateless** - No cross-invocation memory
- **Fast** - Optimized for quick analysis

## Available Sub-Agents

### 1. RequirementsAnalyst

Analyzes and validates requirements for completeness, clarity, and feasibility.

**Use Case:** Validate requirements before creating implementation plan

**Input:**
```python
{
    "requirements": str | dict,  # Raw requirements or structured data
    "context": str (optional),   # Additional context
    "check_feasibility": bool (optional, default=True)
}
```

**Output:**
```python
{
    "completeness_score": float,  # 0.0-1.0
    "clarity_score": float,        # 0.0-1.0
    "missing_elements": List[str], # Missing requirement elements
    "ambiguous_items": List[str],  # Ambiguous requirements
    "conflicts": List[str],        # Conflicting requirements
    "questions": List[str]         # Questions for clarification
}
```

**Example:**
```python
from integrations.bmad.subagents import RequirementsAnalyst

analyst = RequirementsAnalyst(project_dir=Path("/path/to/project"))
result = analyst.analyze({
    "requirements": "As a user, I want to log in...",
    "context": "User authentication feature"
})

if result.data['completeness_score'] < 0.7:
    print(f"Missing elements: {result.data['missing_elements']}")
    print(f"Questions: {result.data['questions']}")
```

### 2. CodebaseAnalyzer

Explores and understands codebase structure, identifies relevant files, and analyzes patterns.

**Use Case:** Find relevant files before implementing a feature

**Input:**
```python
{
    "task": str,                    # Task description
    "search_terms": List[str] (optional),  # Keywords to search
    "file_patterns": List[str] (optional), # File glob patterns
    "max_depth": int (optional, default=5) # Max directory depth
}
```

**Output:**
```python
{
    "project_structure": Dict,      # Directory tree summary
    "relevant_files": List[str],    # Files relevant to task
    "file_patterns": Dict,          # Detected patterns
    "tech_stack": List[str],        # Technologies detected
    "entry_points": List[str],      # Main entry points
    "conventions": List[str]        # Coding conventions detected
}
```

**Example:**
```python
from integrations.bmad.subagents import CodebaseAnalyzer

analyzer = CodebaseAnalyzer(project_dir=Path("/path/to/project"))
result = analyzer.analyze({
    "task": "Add user authentication",
    "search_terms": ["auth", "user", "login"],
    "max_depth": 5
})

print(f"Tech Stack: {result.data['tech_stack']}")
print(f"Relevant Files: {result.data['relevant_files']}")
```

### 3. TechnicalEvaluator

Evaluates technical decisions, assesses risks, and recommends best practices.

**Use Case:** Evaluate architecture decisions before implementation

**Input:**
```python
{
    "decision": str,                    # Technical decision to evaluate
    "context": str (optional),          # Additional context
    "alternatives": List[str] (optional), # Alternative approaches
    "constraints": List[str] (optional)  # Known constraints
}
```

**Output:**
```python
{
    "risk_level": str,          # "low", "medium", "high"
    "risk_factors": List[str],  # Identified risk factors
    "pros": List[str],          # Advantages
    "cons": List[str],          # Disadvantages
    "best_practices": List[str], # Best practice recommendations
    "security_concerns": List[str],
    "performance_concerns": List[str],
    "scalability_concerns": List[str],
    "alternative_recommendations": List[str]
}
```

**Example:**
```python
from integrations.bmad.subagents import TechnicalEvaluator

evaluator = TechnicalEvaluator(project_dir=Path("/path/to/project"))
result = evaluator.analyze({
    "decision": "Use JWT tokens for authentication",
    "context": "Web application with REST API",
    "alternatives": ["Server-side sessions with Redis"],
    "constraints": ["Must support OAuth 2.0"]
})

print(f"Risk Level: {result.data['risk_level']}")
print(f"Security Concerns: {result.data['security_concerns']}")
print(f"Best Practices: {result.data['best_practices']}")
```

## Integration with Main Agents

### Using SubAgentInvoker Helper

The `SubAgentInvoker` class provides a convenient interface for main agents:

```python
from integrations.bmad.subagents.examples import SubAgentInvoker

invoker = SubAgentInvoker(project_dir, spec_dir)

# Before planning
req_analysis = invoker.analyze_requirements(requirements_text)
if req_analysis.issues:
    # Handle issues before continuing
    print(f"Issues: {req_analysis.issues}")

# Before implementation
code_analysis = invoker.find_relevant_files(task_description)
relevant_files = code_analysis.data['relevant_files']
# Use relevant_files in context

# For architecture decisions
eval_result = invoker.evaluate_decision(decision_text)
if eval_result.data['risk_level'] == 'high':
    # Request user confirmation
    print("High-risk decision - user approval needed")
```

### Direct Invocation

For more control, invoke sub-agents directly:

```python
from pathlib import Path
from integrations.bmad.subagents import RequirementsAnalyst, SubAgentResult

analyst = RequirementsAnalyst(project_dir=Path.cwd())

result: SubAgentResult = analyst.analyze({
    "requirements": requirements_text,
    "context": "Feature description",
})

if result.success:
    # Process results
    data = result.data
    confidence = result.confidence
    issues = result.issues
    recommendations = result.recommendations
else:
    # Handle failure
    print(f"Analysis failed: {result.reasoning}")
```

## Testing

Run the included tests to verify sub-agent functionality:

```bash
cd apps/backend
python3 -m integrations.bmad.subagents.test_subagents
```

## Examples

See `examples.py` for comprehensive usage examples, including:
- Individual sub-agent invocation
- Combined workflow using multiple sub-agents
- Integration patterns for main agents

Run examples:
```bash
cd apps/backend
python3 -m integrations.bmad.subagents.examples
```

## Sub-Agent Result Structure

All sub-agents return a `SubAgentResult` object with:

```python
@dataclass
class SubAgentResult:
    success: bool                    # Whether analysis completed successfully
    data: Dict[str, Any]             # Analysis results (structure varies by sub-agent)
    reasoning: str                   # Explanation of findings and methodology
    confidence: float                # Confidence score (0.0-1.0)
    issues: List[str]                # List of issues or concerns found
    recommendations: List[str]       # List of recommendations
    metadata: Dict[str, Any]         # Additional metadata (execution time, tokens used, etc.)
```

## Best Practices

1. **Check Result Success**: Always check `result.success` before using data
2. **Review Confidence**: Use `result.confidence` to assess reliability
3. **Handle Issues**: Act on `result.issues` before proceeding
4. **Follow Recommendations**: Apply `result.recommendations` when appropriate
5. **Chain Sub-Agents**: Use multiple sub-agents for comprehensive analysis

## Architecture

```
main_agent (planner/architect/coder)
    ↓
SubAgentInvoker (optional helper)
    ↓
Sub-Agent (RequirementsAnalyst/CodebaseAnalyzer/TechnicalEvaluator)
    ↓
SubAgentResult (success, data, reasoning, confidence, issues, recommendations)
```

## Adding New Sub-Agents

To add a new sub-agent:

1. Create a new file in this directory (e.g., `performance_analyzer.py`)
2. Extend the `SubAgent` base class
3. Implement the `analyze()` method
4. Define `name` and `description` properties
5. Add to `__init__.py` exports
6. Add tests to `test_subagents.py`
7. Add examples to `examples.py`

Example template:

```python
from .base import SubAgent, SubAgentResult

class MySubAgent(SubAgent):
    @property
    def name(self) -> str:
        return "My Sub-Agent"

    @property
    def description(self) -> str:
        return "What this sub-agent does"

    def analyze(self, input_data: Dict[str, Any]) -> SubAgentResult:
        # Your analysis logic here
        return SubAgentResult(
            success=True,
            data={"key": "value"},
            reasoning="Analysis explanation",
            confidence=0.8
        )
```

## Future Enhancements

Potential sub-agents to add:
- **DependencyAnalyzer** - Analyze dependencies and identify update needs
- **PerformanceAnalyzer** - Identify performance bottlenecks
- **SecurityScanner** - Deep security analysis
- **TestCoverageAnalyzer** - Analyze test coverage and suggest tests
- **DocumentationAnalyzer** - Assess documentation quality
- **APIDesignEvaluator** - Evaluate API design decisions

## References

- **BMad Method Documentation**: https://docs.bmad-method.org/
- **Main Agents**: `apps/backend/agents/`
- **Spec Agents**: `apps/backend/spec_agents/`
