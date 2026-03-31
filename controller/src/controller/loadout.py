"""Agent loadout — defines the complete environment for an agent pod.

An AgentLoadout specifies everything an agent needs:
- Skills (injected via Redis for prompt context)
- Mounted files (written to agent workspace filesystem)
- MCP config (merged into mcp.json)
- Environment variables
- CLAUDE.md additions (appended to agent's CLAUDE.md)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentLoadout:
    """Complete environment specification for an agent pod."""
    thread_id: str

    # Skills to inject via existing Redis skill injection path
    # Format: [{name: str, content: str}]
    skills: list[dict] = field(default_factory=list)

    # Files to mount in the agent's workspace
    # Key = relative path (e.g. ".claude/skills/tdd/SKILL.md"), Value = content
    mounted_files: dict[str, str] = field(default_factory=dict)

    # MCP config entries to merge into agent's mcp.json
    mcp_config: dict = field(default_factory=dict)

    # Extra environment variables for the agent container
    env_vars: dict[str, str] = field(default_factory=dict)

    # Text blocks to append to the agent's CLAUDE.md
    claude_md_additions: list[str] = field(default_factory=list)

    @property
    def total_skill_chars(self) -> int:
        return sum(len(s.get("content", "")) for s in self.skills)

    @property
    def total_mounted_files(self) -> int:
        return len(self.mounted_files)
