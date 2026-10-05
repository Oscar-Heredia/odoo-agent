"""Smoke test against a real database, using the same tools as the agent.

    .venv/bin/python scripts/smoke.py <profile> [--review]

Use a test or staging database. With --review it also sends a trivial query to
the database's AI (in Odoo 20 this spends credits).
"""

import asyncio
import sys
import time

from mcp.server.mcpserver.exceptions import ToolError

from odoo_agent import server


async def step(label, coro):
    start = time.monotonic()
    try:
        result = await coro
    except ToolError as e:
        print(f"ERROR {label}: {e}")
        return None
    print(f"OK    {label} ({time.monotonic() - start:.1f} s, {len(result)} characters)")
    return result


async def main(profile: str, with_review: bool) -> int:
    info = await step("server_info", server.server_info(profile))
    if info is None:
        return 1
    print(info[:800], "\n")
    await step("search_read res.partner", server.search_read("res.partner", ["name", "email"], limit=3))
    await step("search_read count_only", server.search_read("res.partner", ["id"], count_only=True))
    await step("fields res.partner", server.fields("res.partner", field_names=["name", "email"]))
    await step("model_info res.partner (email filter)", server.model_info("res.partner", name_filter="email"))
    await step("get_view res.partner form", server.get_view("res.partner", "form"))
    await step("group_by res.partner by country", server.group_by("res.partner", ["country_id"], limit=5))
    try:
        await server.search_read("res.users", ["login", "password"])
        print("FAIL  the guard allowed reading password")
        return 1
    except ToolError:
        print("OK    the guard rejects reading password")
    if with_review:
        answer = await step(
            "ask_odoo_ai (trivial query)",
            server.ask_odoo_ai(
                problem="Connection test of the review.",
                steps=["Do nothing: this is a test."],
                evidence="None.",
                models=["res.partner"],
            ),
        )
        if answer:
            print(answer[:1500])
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(asyncio.run(main(sys.argv[1], "--review" in sys.argv)))
