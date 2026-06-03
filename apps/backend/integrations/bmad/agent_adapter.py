#!/usr/bin/env python3
"""
Agent Adapter - BMad Method Integration
========================================

Loads BMad-style persona templates and injects them into agent prompts.

This adapter allows for:
1. Loading personas from template files
2. Injecting personas into existing prompts programmatically
3. Customizing persona behavior per-project
"""

from pathlib import Path


class AgentPersona:
    """Represents a BMad-style agent persona with identity, principles, and communication style."""

    def __init__(self, name: str, role: str, identity: str, communication_style: str,
                 principles: list[str], critical_actions: list[str]):
        self.name = name
        self.role = role
        self.identity = identity
        self.communication_style = communication_style
        self.principles = principles
        self.critical_actions = critical_actions

    def to_markdown(self) -> str:
        """Convert persona to markdown format for injection into prompts."""
        md = "## YOUR PERSONA\n\n"
        md += f"You are **{self.name}**, {self.role}.\n\n"
        md += f"### YOUR IDENTITY\n{self.identity}\n\n"
        md += f"### YOUR COMMUNICATION STYLE\n{self.communication_style}\n\n"
        md += "### YOUR PRINCIPLES\n"
        for i, principle in enumerate(self.principles, 1):
            md += f"{i}. {principle}\n"
        md += "\n### CRITICAL ACTIONS\n"
        for action in self.critical_actions:
            md += f"- {action}\n"
        return md


class AgentAdapter:
    """Adapter for loading and injecting BMad-style personas into agent prompts."""

    def __init__(self, personas_dir: Path | None = None):
        """
        Initialize the agent adapter.

        Args:
            personas_dir: Directory containing persona template files.
                         Defaults to integrations/bmad/templates/personas/
        """
        if personas_dir is None:
            backend_dir = Path(__file__).parent.parent.parent
            personas_dir = backend_dir / "integrations" / "bmad" / "templates" / "personas"

        self.personas_dir = personas_dir
        self._persona_cache: dict[str, AgentPersona] = {}

    def load_persona(self, persona_name: str) -> AgentPersona | None:
        """
        Load a persona from a template file.

        Args:
            persona_name: Name of the persona file (e.g., 'pm_persona', 'developer_persona')

        Returns:
            AgentPersona object or None if file not found
        """
        # Check cache first
        if persona_name in self._persona_cache:
            return self._persona_cache[persona_name]

        persona_file = self.personas_dir / f"{persona_name}.md"
        if not persona_file.exists():
            return None

        content = persona_file.read_text()

        # Simple parsing - assumes persona files follow the template structure
        # In production, you might want a more robust parser
        persona = self._parse_persona_markdown(content)
        self._persona_cache[persona_name] = persona
        return persona

    def _parse_persona_markdown(self, content: str) -> AgentPersona | None:
        """Parse persona markdown content into AgentPersona object."""
        lines = content.split('\n')

        # Extract persona name and role from first header
        # Format: "# [Role] Persona: [Name]"
        name = ""
        role = ""
        for line in lines[:5]:
            if line.startswith("# ") and "Persona:" in line:
                parts = line[2:].split("Persona:")
                if len(parts) == 2:
                    role = parts[0].strip()
                    name = parts[1].strip()
                break

        # Extract identity (under ## YOUR IDENTITY)
        identity = self._extract_section(content, "## YOUR IDENTITY")

        # Extract communication style
        communication_style = self._extract_section(content, "## YOUR COMMUNICATION STYLE")

        # Extract principles (numbered list under ## YOUR PRINCIPLES)
        principles = self._extract_list_section(content, "## YOUR PRINCIPLES")

        # Extract critical actions (bullet list under ## CRITICAL ACTIONS)
        critical_actions = self._extract_list_section(content, "## CRITICAL ACTIONS YOU ALWAYS TAKE")

        if not name:
            return None

        return AgentPersona(
            name=name,
            role=role,
            identity=identity,
            communication_style=communication_style,
            principles=principles,
            critical_actions=critical_actions
        )

    def _extract_section(self, content: str, header: str) -> str:
        """Extract content from a markdown section."""
        lines = content.split('\n')
        in_section = False
        section_lines = []

        for line in lines:
            if line.strip() == header:
                in_section = True
                continue
            if in_section:
                if line.startswith('##'):  # Next section
                    break
                if line.strip():
                    section_lines.append(line.strip())

        return '\n'.join(section_lines)

    def _extract_list_section(self, content: str, header: str) -> list[str]:
        """Extract a list from a markdown section."""
        lines = content.split('\n')
        in_section = False
        items = []

        for line in lines:
            if line.strip() == header:
                in_section = True
                continue
            if in_section:
                if line.startswith('##'):  # Next section
                    break
                line = line.strip()
                # Handle both numbered (1. ) and bulleted (- ) lists
                if line.startswith(('- ', '* ', '1. ', '2. ', '3. ', '4. ', '5. ', '6. ')):
                    # Remove list marker
                    for prefix in ['- ', '* ', '1. ', '2. ', '3. ', '4. ', '5. ', '6. ']:
                        if line.startswith(prefix):
                            items.append(line[len(prefix):])
                            break

        return items

    def inject_persona(self, base_prompt: str, persona_name: str) -> str:
        """
        Inject a persona into an agent prompt.

        Args:
            base_prompt: The base agent prompt
            persona_name: Name of the persona to inject (e.g., 'pm_persona')

        Returns:
            Enhanced prompt with persona injected after the role header
        """
        persona = self.load_persona(persona_name)
        if not persona:
            return base_prompt  # Return unmodified if persona not found

        persona_md = persona.to_markdown()

        # Find where to inject (after first ## header and key principle)
        lines = base_prompt.split('\n')
        insert_index = 0

        # Find the end of the role header section (before first ---)
        for i, line in enumerate(lines):
            if line.strip() == '---':
                insert_index = i
                break

        # Insert persona markdown
        enhanced_lines = lines[:insert_index] + ['', persona_md, ''] + lines[insert_index:]
        return '\n'.join(enhanced_lines)


# Convenience functions

def get_agent_adapter() -> AgentAdapter:
    """Get a singleton instance of the AgentAdapter."""
    return AgentAdapter()


def inject_persona_into_prompt(prompt: str, agent_type: str) -> str:
    """
    Inject the appropriate persona into an agent prompt.

    Args:
        prompt: Base agent prompt
        agent_type: Type of agent ('planner', 'coder', 'qa_reviewer', 'qa_fixer', 'architect')

    Returns:
        Enhanced prompt with persona
    """
    persona_map = {
        'planner': 'pm_persona',
        'coder': 'developer_persona',
        'qa_reviewer': 'qa_persona',
        'qa_fixer': 'qa_fixer_persona',
        'architect': 'architect_persona',
    }

    persona_name = persona_map.get(agent_type)
    if not persona_name:
        return prompt

    adapter = get_agent_adapter()
    return adapter.inject_persona(prompt, persona_name)


if __name__ == "__main__":
    # Test the adapter
    adapter = AgentAdapter()

    # Test loading PM persona
    pm = adapter.load_persona('pm_persona')
    if pm:
        print(f"Loaded persona: {pm.name} ({pm.role})")
        print(f"Principles: {len(pm.principles)}")
        print(f"Critical Actions: {len(pm.critical_actions)}")
        print("\nMarkdown output:\n")
        print(pm.to_markdown())
    else:
        print("Failed to load PM persona")
