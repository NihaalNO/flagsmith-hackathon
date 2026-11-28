"""AI-proposed A/B experiments for a repository.

Reads a repository snapshot (GitHub tarball), builds a compact digest (file tree plus the
most relevant source files) and asks the configured LLM for experiments worth running:
a multivariate flag name, the control and variant values, the conversion event and where in
the code it would go. Nothing is created here; the launch route does that after a person
picks the proposals.
"""

import io
import re
import tarfile

from app.services.code_scan import MAX_FILE_BYTES, _wanted

FLAG_NAME = re.compile(r"^[a-z][a-z0-9_]{2,59}$")
KEY = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")

DIGEST_BUDGET = 18_000      # characters of file content sent to the LLM (Groq's free tier limits tokens/minute)
PER_FILE = 1_800
MAX_TREE_PATHS = 250
SOURCE_EXT = (".tsx", ".jsx", ".ts", ".js", ".vue", ".svelte", ".astro", ".html", ".py", ".rb", ".php", ".go")
MANIFESTS = ("readme.md", "package.json", "pyproject.toml", "requirements.txt")
# Where user-facing choices usually live, and words that point at conversion moments.
UI_HINTS = ("page", "pages/", "app/", "routes/", "views/", "components/", "templates/", "screens/", "landing", "home", "index")
CONVERSION_WORDS = ("checkout", "signup", "sign-up", "register", "pricing", "plan", "cart", "subscribe", "onboard",
                    "cta", "button", "hero", "trial", "upgrade", "purchase", "buy", "download", "search", "form")

MAX_PROPOSALS = 3

PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "flag_name": {"type": "string", "description": "snake_case, 3-60 chars, starts with a letter"},
                    "title": {"type": "string", "description": "Short experiment name"},
                    "hypothesis": {"type": "string"},
                    "primary_metric": {"type": "string", "description": "snake_case conversion event, e.g. signup_completed"},
                    "control": {"type": "object", "properties": {
                        "key": {"type": "string"}, "value": {"type": "string"}, "description": {"type": "string"}},
                        "required": ["key", "value", "description"]},
                    "variants": {"type": "array", "items": {"type": "object", "properties": {
                        "key": {"type": "string"}, "value": {"type": "string"}, "description": {"type": "string"}},
                        "required": ["key", "value", "description"]}},
                    "files": {"type": "array", "items": {"type": "string"},
                              "description": "Repository paths where the flag would be read"},
                    "rationale": {"type": "string"},
                },
                "required": ["flag_name", "title", "hypothesis", "primary_metric", "control", "variants", "files",
                             "rationale"],
            },
        },
    },
    "required": ["proposals"],
}

SYSTEM = (
    "You are a product growth engineer. You design A/B tests that a small team can wire up in an afternoon. "
    "Only propose experiments grounded in code you were shown: a user-facing choice (copy, layout, default, "
    "ordering, a step in a flow) whose effect shows up in a measurable conversion. Never invent files."
)


def _score(path: str) -> int:
    p = path.lower()
    score = 0
    if p.rsplit("/", 1)[-1] in MANIFESTS:
        score += 100
    if p.endswith(SOURCE_EXT):
        score += 10
        score += 15 * sum(h in p for h in UI_HINTS)
        score += 25 * sum(w in p for w in CONVERSION_WORDS)
    if any(x in p for x in ("test", "spec", "mock", "fixture", ".config.", "migrations/", ".d.ts")):
        score -= 40
    return score - p.count("/")  # prefer shallower files on ties


def repo_digest(tarball: bytes) -> tuple[str, list[str]]:
    """(digest text, every path in the repo) for the LLM prompt."""
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile() or m.size > MAX_FILE_BYTES:
                continue
            path = m.name.split("/", 1)[1] if "/" in m.name else m.name  # strip GitHub's top folder
            if not path or not _wanted(path):
                continue
            fh = tar.extractfile(m)
            if fh is None:
                continue
            try:
                files[path] = fh.read().decode("utf-8")
            except UnicodeDecodeError:
                continue
    paths = sorted(files)
    tree = "\n".join(paths[:MAX_TREE_PATHS]) + (f"\n... {len(paths) - MAX_TREE_PATHS} more" if len(paths) > MAX_TREE_PATHS else "")
    parts, used = [], 0
    for path in sorted(paths, key=_score, reverse=True):
        if _score(path) <= 0 or used >= DIGEST_BUDGET:
            break
        body = files[path][:PER_FILE]
        if len(files[path]) > PER_FILE:
            body += "\n... (truncated)"
        chunk = f"\n--- {path} ---\n{body}\n"
        if used + len(chunk) > DIGEST_BUDGET:
            continue
        parts.append(chunk)
        used += len(chunk)
    return f"FILE TREE\n{tree}\n\nSELECTED FILES\n{''.join(parts)}", paths


def _snake(value: str, limit: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:limit]
    return s if s and s[0].isalpha() else f"x_{s}"[:limit]


def clean_proposals(raw: dict, paths: list[str], taken_names: set[str]) -> list[dict]:
    """Normalise and validate LLM output; drop anything unusable instead of failing."""
    out: list[dict] = []
    names = {n.lower() for n in taken_names}
    known = set(paths)
    for p in (raw or {}).get("proposals", []) or []:
        try:
            name = _snake(str(p["flag_name"]))
            if not FLAG_NAME.match(name) or name in names:
                continue
            control = {"key": "control", "value": str(p["control"]["value"])[:200],
                       "description": str(p["control"].get("description", ""))[:500]}
            variants, seen_keys, seen_values = [], {"control"}, {control["value"]}
            for v in p.get("variants") or []:
                key = _snake(str(v.get("key") or v.get("value") or "variant"), 40)
                value = str(v.get("value", ""))[:200]
                if not KEY.match(key) or key in seen_keys or not value or value in seen_values:
                    continue
                seen_keys.add(key)
                seen_values.add(value)
                variants.append({"key": key, "value": value, "description": str(v.get("description", ""))[:500]})
            if not variants or not control["value"]:
                continue
            metric = _snake(str(p["primary_metric"]), 100)
            out.append({
                "flag_name": name,
                "title": str(p.get("title") or name)[:200],
                "hypothesis": str(p.get("hypothesis") or "")[:2000],
                "primary_metric": metric,
                "control": control,
                "variants": variants[:3],
                # Only paths that exist: the model must not send people to invented files.
                "files": [f for f in (p.get("files") or []) if f in known][:5],
                "rationale": str(p.get("rationale") or "")[:1000],
            })
            names.add(name)
        except (KeyError, TypeError, AttributeError):
            continue
        if len(out) >= MAX_PROPOSALS:
            break
    return out


def propose(provider, repo_full_name: str, tarball: bytes, taken_names: set[str], focus: str | None = None) -> list[dict]:
    digest, paths = repo_digest(tarball)
    ask = (f"Repository: {repo_full_name}\n"
           f"Existing flag names (don't reuse): {', '.join(sorted(taken_names)) or 'none'}\n"
           + (f"The team wants to focus on: {focus}\n" if focus else "")
           + f"\nPropose up to {MAX_PROPOSALS} A/B experiments, best first. For each: a multivariate flag "
             "(snake_case name), the control value (today's behaviour) and 1-2 variant values (short strings the "
             "code switches on, e.g. button copy or a layout name), the conversion event to count, the files "
             "where the flag would be read, and why the test is worth running.\n\n" + digest)
    return clean_proposals(provider.complete_json(system=SYSTEM, prompt=ask, schema=PROPOSAL_SCHEMA,
                                                  context={"repository": repo_full_name}), paths, taken_names)


def even_split(n_variants: int) -> tuple[float, list[float]]:
    """(control %, [variant %...]) splitting traffic evenly; variants get 2-decimal shares."""
    share = round(100 / (n_variants + 1), 2)
    return round(100 - share * n_variants, 2), [share] * n_variants
