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

from prompts import CRON_JOB_SKILL, TIME_MCP_SKILL, REPORT_GENERATION_SKILL

# Skill definitions
# Each key matches the MCPServer.name of a default server.
SKILLS: dict[str, str] = {
    "time": TIME_MCP_SKILL,
    "cron_job": CRON_JOB_SKILL,
    "pdf_generator": REPORT_GENERATION_SKILL,
}


class SkillsRegistry:
    @staticmethod
    def get_skills_block(server_names: list[str], bot_id: str, timezone: str) -> str:
        """
        Returns a formatted skills block for the given server names.
        Only includes entries that exist in SKILLS — unknown names are
        silently ignored (custom MCPServers don't need skill descriptions).

        Returns an empty string if no matching skills are found.

        Args:
            server_names: List of active MCPServer.name values for this bot.
                          Typically ["time", "cron_job"] for default servers,
                          plus any custom ones the user has added.
            bot_id: Bot ID will be used as a context to the default CronJob MCP server.
            timezone: App's default timezone will be used as a context in the default Time MCP server
        """

        matched = []

        for name in server_names:
            skill = SKILLS.get(name)

            if skill:
                if name == "time":
                    skill = skill.format(timezone=timezone)
                elif name in {"cron_job", "pdf_generator"}:
                    skill = skill.format(bot_id=bot_id)
                matched.append(skill)

        if not matched:
            return ""

        return "# Skills\n\n" + "\n\n".join(matched)

    @staticmethod
    def known_skills() -> list[str]:
        """
        Returns the list of server names that have a registered skill.
        Useful for admin display or debugging.
        """

        return list(SKILLS.keys())
