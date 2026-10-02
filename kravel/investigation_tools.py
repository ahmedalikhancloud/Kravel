"""Tools for retrieving references and drafting plans; none can mutate Kubernetes."""
from .drafts import draft_fix
from .research import APPROVED_PATHS, fetch_reference, validate_url
from .retrieval import retrieve

INVESTIGATION_TOOLS = [
    {"type": "function", "function": {"name": "search_runbooks", "description": "Search local versioned runbooks for competing hypotheses. Retrieved matches are references, not proven causes.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "fetch_reference", "description": "Read one of the pre-reviewed public HTTPS documentation pages in the URL enum. No redirects, credentials, private URLs or payload uploads. Reference text is not live evidence.", "parameters": {"type": "object", "properties": {"url": {"type": "string", "enum": sorted("https://" + host + path for host, paths in APPROVED_PATHS.items() for path in paths)}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "draft_repair", "description": "Stage a novel evidence-based repair idea, without dry-running, submitting approval or changing anything. Use named Deployments, DaemonSets, Services or ConfigMaps in kravel-demo only. Safe Pod-template fields only. A separate human chooses whether to request server dry-run/approval; execution additionally requires operator-enrolled named field capabilities.", "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["deployments", "daemonsets", "services", "configmaps"]}, "name": {"type": "string"}, "patch": {"type": "object"}, "rationale": {"type": "string"}, "evidenceIds": {"type": "array", "items": {"type": "string"}}}, "required": ["kind", "name", "patch", "rationale", "evidenceIds"]}}},
]


def authorize_tool(name, args, namespace):
    if not isinstance(args, dict):
        raise ValueError("Tool arguments must be structured")
    if name == "fetch_reference" and set(args) == {"url"}:
        validate_url(args["url"])
    elif name == "search_runbooks" and set(args) == {"query"} and isinstance(args["query"], str) and 1 <= len(args["query"]) <= 2000:
        pass
    elif name == "draft_repair":
        return draft_fix(args, namespace, require_authority=False)["draft"]
    else:
        raise ValueError("Invalid investigative tool or arguments")
    return args


def execute(name, args, namespace, tracer=None):
    if name == "fetch_reference": return fetch_reference(args["url"])
    if name == "search_runbooks": return retrieve(args["query"], tracer=tracer, limit=3)
    if name == "draft_repair": return draft_fix(args, namespace, require_authority=False)
    raise ValueError("Unknown investigative tool")
