"""Offline writer. It assembles a short proposal from the job post and the freelancer's own profile.
It is plain on purpose. It quotes the job post instead of paraphrasing it, and it only claims what the
profile says, so nothing can be invented. When it has nothing honest to say it leaves that part out
and tells the freelancer."""
from __future__ import annotations

import re

from .profile import Profile
from .writer import Draft

KNOWN_SKILLS = [
    "python", "fastapi", "django", "flask", "javascript", "typescript", "react", "next.js", "node", "laravel", "php", "n8n", "zapier",
    "openai", "claude", "llm", "rag", "chatbot", "scraping", "api", "webhook", "stripe", "aws", "docker", "postgres", "mysql", "sqlite",
    "wordpress", "shopify", "automation", "mcp", "pdf", "crm", "slack", "airtable", "supabase",
]
DISPLAY = {"fastapi": "FastAPI", "javascript": "JavaScript", "typescript": "TypeScript", "next.js": "Next.js", "php": "PHP", "openai": "OpenAI",
           "llm": "LLM", "rag": "RAG", "api": "API", "aws": "AWS", "mysql": "MySQL", "sqlite": "SQLite", "wordpress": "WordPress", "mcp": "MCP",
           "pdf": "PDF", "crm": "CRM", "n8n": "n8n", "postgres": "Postgres"}
FILLER = {"the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with", "my", "our", "your", "is", "are", "that", "this", "it", "we", "i", "you", "be"}
CLOSE = {
    "friendly": "Happy to start with a short call, or a small first piece of work, whichever suits you.",
    "formal": "I would be glad to discuss the scope with you at a time that suits you.",
}
QUESTIONS = [
    (re.compile(r"[$£€]\s?\d|budget|per hour|/hr|fixed price", re.I), "Do you have a rough budget range in mind?"),
    (re.compile(r"deadline|by (monday|tuesday|wednesday|thursday|friday|next|the end)|asap|within \d|\d+\s*(day|week|month)", re.I), "When do you need this working by?"),
]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9.+#]+", text.lower()) if w not in FILLER and len(w) > 2}


def _sentences(job_text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", job_text) if s.strip()]


def _first_sentence(job_text: str) -> str:
    first = _sentences(job_text)[0] if _sentences(job_text) else ""
    return first if len(first) <= 160 else first[:157].rsplit(" ", 1)[0] + "..."


def _stack(job_text: str) -> list[str]:
    lowered = job_text.lower()
    found = [(lowered.find(s), s) for s in KNOWN_SKILLS if re.search(rf"(?<![a-z]){re.escape(s)}(?![a-z])", lowered)]
    return [s for _, s in sorted(found)]


def _shown(skill: str, profile: Profile) -> str:
    own = next((s for s in profile.skills if s.lower() == skill), None)
    return own or DISPLAY.get(skill, skill.capitalize())


def _best_proofs(job_text: str, profile: Profile, limit: int = 2) -> list[str]:
    job_words = _words(job_text)
    scored = sorted(((len(_words(p) & job_words), -i, p) for i, p in enumerate(profile.proof_points)), reverse=True)
    return [p for score, _, p in scored if score > 0][:limit]


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def write(job_text: str, profile: Profile) -> Draft:
    mine = {s.lower() for s in profile.skills} | _words(profile.headline)
    shared = [s for s in _stack(job_text) if s in mine]
    warnings: list[str] = []
    parts = [f'Hello,\n\nYour post says: "{_first_sentence(job_text)}"']
    if shared:
        names = [_shown(s, profile) for s in shared]
        parts.append(f"You mention {_join(names)}, and {'that is' if len(names) == 1 else 'those are'} what I work with.")
    proofs = _best_proofs(job_text, profile)
    if proofs:
        parts.append("Relevant work: " + " ".join(p.rstrip(".") + "." for p in proofs))
    else:
        warnings.append("None of your proof points matches this job, so the draft has none. Add one in your own words, or think about whether this job suits you.")
    parts.append(next((q for rx, q in QUESTIONS if not rx.search(job_text)), "What does a good result look like for you in the first two weeks?"))
    parts.append(CLOSE[profile.tone])
    asked = [s for s in _sentences(job_text) if s.endswith("?")][:2]
    for question in asked:
        warnings.append(f'The post asks you directly: "{question}" Answer it in your own words.')
    return Draft("\n\n".join(parts) + f"\n\n{profile.name}", "offline", warnings)
