"""
HTML Plan Review Generator
===========================

Generates an interactive HTML version of the implementation plan for better readability.
"""

import json
import re
from datetime import datetime
from pathlib import Path

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    JINJA2_AVAILABLE = True
except ImportError:
    JINJA2_AVAILABLE = False


def extract_overview_from_spec(spec_content: str) -> str:
    """Extract the overview section from spec.md.

    Args:
        spec_content: The full spec.md content

    Returns:
        The overview text or a fallback message
    """
    # Try to find Overview section
    overview_match = re.search(
        r"## Overview\s*\n(.*?)(?=\n##|\Z)", spec_content, re.DOTALL | re.IGNORECASE
    )
    if overview_match:
        overview = overview_match.group(1).strip()
        # Limit to first paragraph or 300 chars
        first_para = overview.split("\n\n")[0]
        if len(first_para) > 300:
            return first_para[:297] + "..."
        return first_para

    # Fallback: try to extract from first heading content
    first_content = re.search(r"^#[^#].*?\n\n(.*?)(?=\n##|\Z)", spec_content, re.DOTALL | re.MULTILINE)
    if first_content:
        text = first_content.group(1).strip()
        if len(text) > 300:
            return text[:297] + "..."
        return text

    return "No overview available. See spec.md for full details."


def extract_success_criteria(spec_content: str) -> list[str]:
    """Extract success criteria from spec.md.

    Args:
        spec_content: The full spec.md content

    Returns:
        List of success criteria strings
    """
    criteria = []

    # Find Success Criteria section
    criteria_match = re.search(
        r"## Success Criteria\s*\n(.*?)(?=\n##|\Z)",
        spec_content,
        re.DOTALL | re.IGNORECASE
    )

    if criteria_match:
        criteria_text = criteria_match.group(1)
        # Extract checkbox items or list items
        checkbox_items = re.findall(
            r"^\s*[-*]\s*\[[ x]\]\s*(.+)$",
            criteria_text,
            re.MULTILINE
        )
        if checkbox_items:
            criteria = checkbox_items
        else:
            # Try regular list items
            list_items = re.findall(
                r"^\s*[-*]\s+(.+)$",
                criteria_text,
                re.MULTILINE
            )
            criteria = list_items

    return criteria[:10]  # Limit to first 10


def extract_workflow_type(spec_content: str) -> str:
    """Extract workflow type from spec.md.

    Args:
        spec_content: The full spec.md content

    Returns:
        Workflow type string or "standard"
    """
    workflow_match = re.search(
        r"\*\*Type\*\*:\s*(\w+)",
        spec_content,
        re.IGNORECASE
    )
    if workflow_match:
        return workflow_match.group(1).lower()
    return "standard"


def calculate_progress(plan: dict) -> int:
    """Calculate overall progress percentage.

    Args:
        plan: The implementation plan dictionary

    Returns:
        Progress percentage (0-100)
    """
    phases = plan.get("phases", [])
    if not phases:
        return 0

    total_subtasks = sum(len(p.get("subtasks", [])) for p in phases)
    if total_subtasks == 0:
        return 0

    completed_subtasks = sum(
        1
        for p in phases
        for c in p.get("subtasks", [])
        if c.get("status") == "completed"
    )

    return int((completed_subtasks / total_subtasks) * 100)


def determine_phase_status(phase: dict) -> str:
    """Determine the status of a phase based on its subtasks.

    Args:
        phase: Phase dictionary

    Returns:
        Status string: 'completed', 'in_progress', or 'pending'
    """
    subtasks = phase.get("subtasks", [])
    if not subtasks:
        return "pending"

    completed = sum(1 for c in subtasks if c.get("status") == "completed")
    in_progress = sum(1 for c in subtasks if c.get("status") == "in_progress")

    if completed == len(subtasks):
        return "completed"
    elif completed > 0 or in_progress > 0:
        return "in_progress"
    else:
        return "pending"


def generate_html_plan_review(spec_dir: Path, output_path: Path | None = None) -> Path:
    """Generate an HTML version of the implementation plan for review.

    Args:
        spec_dir: Path to the spec directory
        output_path: Optional custom output path (default: spec_dir/plan_review.html)

    Returns:
        Path to the generated HTML file

    Raises:
        FileNotFoundError: If required files are missing
        ImportError: If jinja2 is not installed
    """
    if not JINJA2_AVAILABLE:
        raise ImportError(
            "jinja2 is required for HTML generation. Install with: pip install jinja2"
        )

    spec_dir = Path(spec_dir)

    # Read plan
    plan_file = spec_dir / "test_plan.json"
    if not plan_file.exists():
        raise FileNotFoundError(f"test_plan.json not found in {spec_dir}")

    with open(plan_file) as f:
        plan = json.load(f)

    # Read requirements.json for created_at and other metadata
    requirements_file = spec_dir / "requirements.json"
    requirements = {}
    if requirements_file.exists():
        with open(requirements_file) as f:
            requirements = json.load(f)

    # Read spec for overview and criteria
    spec_file = spec_dir / "spec.md"
    overview = ""
    success_criteria = []
    workflow_type = plan.get("workflow_type", "standard")

    if spec_file.exists():
        spec_content = spec_file.read_text(encoding="utf-8")
        overview = extract_overview_from_spec(spec_content)
        success_criteria = extract_success_criteria(spec_content)
        detected_workflow = extract_workflow_type(spec_content)
        if detected_workflow:
            workflow_type = detected_workflow

    # Calculate stats
    phases = plan.get("phases", [])
    total_phases = len(phases)
    total_subtasks = sum(len(p.get("subtasks", [])) for p in phases)
    completed_subtasks = sum(
        1
        for p in phases
        for c in p.get("subtasks", [])
        if c.get("status") == "completed"
    )

    # Add status to each phase
    for phase in phases:
        phase["status"] = determine_phase_status(phase)

    # Prepare template data
    metadata = plan.get("metadata", {})
    summary = plan.get("summary", {})
    parallelism = summary.get("parallelism", {})

    template_data = {
        "spec_name": plan.get("spec_name", spec_dir.name),
        "overview": overview,
        "workflow_type": workflow_type,
        "total_phases": total_phases,
        "total_subtasks": total_subtasks,
        "completed_subtasks": completed_subtasks,
        "complexity": metadata.get("complexity", "standard"),
        "estimated_sessions": metadata.get("estimated_sessions", 1),
        "progress": calculate_progress(plan),
        "phases": phases,
        "success_criteria": success_criteria,
        "created_at": requirements.get("created_at", metadata.get("created_at", "Unknown")),
        "services_involved": plan.get("services_involved", []),
        "recommended_workers": parallelism.get("recommended_workers"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Load template
    template_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("plan_review.html")

    # Render
    html_content = template.render(**template_data)

    # Write output
    if output_path is None:
        output_path = spec_dir / "plan_review.html"
    else:
        output_path = Path(output_path)

    output_path.write_text(html_content, encoding="utf-8")

    return output_path


def open_in_browser(html_path: Path) -> bool:
    """Open the HTML file in the default browser.

    Args:
        html_path: Path to the HTML file

    Returns:
        True if successful, False otherwise
    """
    import subprocess
    import sys

    try:
        if sys.platform == "darwin":  # macOS
            subprocess.run(["open", str(html_path)], check=True)
        elif sys.platform == "win32":  # Windows
            subprocess.run(["cmd", "/c", "start", "", str(html_path)], check=True)
        else:  # Linux
            subprocess.run(["xdg-open", str(html_path)], check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


if __name__ == "__main__":
    import sys

    from ui import print_status

    if len(sys.argv) < 2:
        print("Usage: python html_generator.py <spec_dir> [--open]")
        sys.exit(1)

    spec_dir = Path(sys.argv[1])
    should_open = "--open" in sys.argv

    try:
        html_path = generate_html_plan_review(spec_dir)
        print_status(f"HTML plan generated: {html_path}", "success")

        if should_open:
            if open_in_browser(html_path):
                print_status("Opened in browser", "success")
            else:
                print_status("Could not open browser automatically", "warning")
                print(f"Open manually: file://{html_path.absolute()}")

    except Exception as e:
        print_status(f"Error generating HTML: {e}", "error")
        sys.exit(1)
