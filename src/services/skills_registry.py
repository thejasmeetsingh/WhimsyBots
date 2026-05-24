"""
SkillsRegistry — static capability descriptions for default MCP servers.

Skills are injected into every system prompt to help the LLM understand
what tools it has and when to use them. They are:
  - Always included (never truncated by the token budget)
  - Sized to comfortably fit within the fixed SKILLS_RESERVED_TOKENS budget
    defined in TokenBudgetService (~250 tokens / ~875 chars total)
  - Author-controlled — keep each skill description tight

Adding a new default MCP server:
  1. Add a key to SKILLS matching the MCPServer.name value
  2. Keep the description under ~400 chars
  3. That's it — ContextAssembler picks it up automatically via get_skills_block()
"""

from __future__ import annotations

from prompts import TIME_MCP_SKILL, CRON_JOB_SKILL


# Skill definitions
# Each key matches the MCPServer.name of a default server.
SKILLS: dict[str, str] = {
    "time": TIME_MCP_SKILL,
    "cron_job": CRON_JOB_SKILL,
}


class SkillsRegistry:
    @staticmethod
    def get_skills_block(server_names: list[str]) -> str:
        """
        Returns a formatted skills block for the given server names.
        Only includes entries that exist in SKILLS — unknown names are
        silently ignored (custom MCPServers don't need skill descriptions).

        Returns an empty string if no matching skills are found.

        Args:
            server_names: List of active MCPServer.name values for this bot.
                          Typically ["time", "cron_job"] for default servers,
                          plus any custom ones the user has added.
        """

        matched = [SKILLS[name] for name in server_names if name in SKILLS]

        if not matched:
            return ""

        return "# Bot Skills\n\n" + "\n\n".join(matched)

    @staticmethod
    def known_skills() -> list[str]:
        """
        Returns the list of server names that have a registered skill.
        Useful for admin display or debugging.
        """

        return list(SKILLS.keys())
