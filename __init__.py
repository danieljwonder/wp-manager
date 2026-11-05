"""wp-manager — manage a fleet of WordPress sites from Hermes Agent.

Plugin/theme/core/translation updates with an approval gate and post-update health checks, plus
post and page editing, through the "Website Manager: WP Connector for Hermes" WordPress plugin and
core Application Passwords. Takes no backups.
"""

from __future__ import annotations

from pathlib import Path

from . import approval, schemas, tools

TOOLSET = "wp_manager"


def register(ctx):
    def _get(key, default=None):
        try:
            return ctx.get_config(key, default=default)
        except Exception:
            return default

    tools.set_settings_getter(_get)
    probe = getattr(ctx, "current_cron_execution", None)
    if callable(probe):
        approval.set_cron_probe(probe)

    for schema in schemas.ALL:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=schema,
            handler=tools.HANDLERS[schema["name"]],
            description=schema["description"],
            emoji="🌐",
        )

    ctx.register_hook("pre_tool_call", approval.pre_tool_call)

    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)
