"""The vendored Odoo skills (skills/) and how the agent refers to them."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
AGENT = ROOT / "agents" / "odoo-dev.md"

LINK = re.compile(r"\]\(([^)\s]+)\)")


def frontmatter(path):
    text = path.read_text()
    assert text.startswith("---\n"), f"{path} has no frontmatter"
    return text.split("---\n", 2)[1]


def anchors(path):
    """GitHub-style anchors of a Markdown file's headings."""
    result = set()
    for line in path.read_text().splitlines():
        m = re.match(r"#+\s+(.*)", line)
        if m:
            title = re.sub(r"[^\w\s-]", "", m.group(1).lower())
            result.add(title.strip().replace(" ", "-"))
    return result


SKILL_DIRS = sorted(p for p in SKILLS.iterdir() if p.is_dir())
DOCS = sorted(SKILLS.rglob("*.md"))


def test_vendored_skills():
    assert [p.name for p in SKILL_DIRS] == [
        "odoo-guidelines", "odoo-review", "odoo-security", "odoo-web-guidelines",
    ]


@pytest.mark.parametrize("skill", SKILL_DIRS, ids=lambda p: p.name)
def test_skill_name_matches_folder(skill):
    assert re.search(rf"^name: {re.escape(skill.name)}$", frontmatter(skill / "SKILL.md"), re.M)


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(SKILLS)))
def test_relative_links_resolve(doc):
    for target in LINK.findall(doc.read_text()):
        if target.startswith(("http://", "https://")):
            continue
        path, _, anchor = target.partition("#")
        dest = (doc.parent / path).resolve() if path else doc
        assert dest.is_file(), f"{doc.name}: broken link {target}"
        if anchor:
            assert anchor in anchors(dest), f"{doc.name}: missing anchor {target}"


def test_sibling_skill_references_resolve():
    # odoo-review points at its siblings as `../<skill>/SKILL.md` in code spans.
    for doc in DOCS:
        for ref in re.findall(r"`(\.\./[^`]+)`", doc.read_text()):
            assert (doc.parent / ref).is_file(), f"{doc.name}: broken reference {ref}"


def test_agent_can_invoke_the_skills():
    tools = re.search(r"^tools: (.*)$", frontmatter(AGENT), re.M).group(1)
    assert "Skill" in [t.strip() for t in tools.split(",")]
    named = set(re.findall(r"`(odoo-[a-z-]+)`", AGENT.read_text()))
    assert named >= {p.name for p in SKILL_DIRS}
    assert named <= {p.name for p in SKILL_DIRS}, f"unknown skills: {named - {p.name for p in SKILL_DIRS}}"
