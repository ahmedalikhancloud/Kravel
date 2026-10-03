"""Investigation and repair-review tools; none can approve or apply a change."""
from .drafts import draft_fix
from .research import APPROVED_PATHS, fetch_reference, validate_url
from .retrieval import retrieve
from .image_research import PUBLIC_REPOSITORIES, authorize as authorize_image, search_image_tags, inspect_image_tag

INVESTIGATION_TOOLS = [
    {"type": "function", "function": {"name": "search_image_tags", "description": "Research real published stable-looking image version candidates on Docker Hub, matching Linux architecture. No credentials, private repositories, pulls or writes. Use for bad/missing/obsolete image tags; preserve workload purpose, review migration/config requirements. Availability is not application compatibility.", "parameters": {"type": "object", "properties": {"repository": {"type": "string", "enum": sorted(PUBLIC_REPOSITORIES)}, "prefix": {"type": "string"}, "architecture": {"type": "string", "enum": ["amd64", "arm64"]}}, "required": ["repository"]}}},
    {"type": "function", "function": {"name": "inspect_image_tag", "description": "Verify one exact public image tag, its digest and platforms before proposing a replacement. A not_found result is evidence, not a tool crash. Does not test startup or compatibility.", "parameters": {"type": "object", "properties": {"repository": {"type": "string", "enum": sorted(PUBLIC_REPOSITORIES)}, "tag": {"type": "string"}}, "required": ["repository", "tag"]}}},
    {"type": "function", "function": {"name": "search_runbooks", "description": "Search local versioned runbooks for competing hypotheses. Retrieved matches are references, not proven causes.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "fetch_reference", "description": "Read one of the pre-reviewed public HTTPS documentation pages in the URL enum. No redirects, credentials, private URLs or payload uploads. Reference text is not live evidence.", "parameters": {"type": "object", "properties": {"url": {"type": "string", "enum": sorted("https://" + host + path for host, paths in APPROVED_PATHS.items() for path in paths)}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "draft_repair", "description": "Stage an evidence-based structured repair for existing Deployments, DaemonSets, Services or ConfigMaps in kravel-demo. No change occurs. Use supported fields and independently established correct values, not guesses. Default approval-gated mode needs no per-resource enrollment; server dry-run and human approval are still mandatory.", "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["deployments", "daemonsets", "services", "configmaps"]}, "name": {"type": "string"}, "patch": {"type": "object"}, "rationale": {"type": "string"}, "evidenceIds": {"type": "array", "items": {"type": "string"}}}, "required": ["kind", "name", "patch", "rationale", "evidenceIds"]}}},
    {"type": "function", "function": {"name": "request_repair_approval", "description": "When the operator requests a change, request validation and Slack/Local Slack human review for exactly one observed catalog fixId, draftId, or general planId staged in THIS investigation. This queues review only after all guards pass; it cannot approve or execute. Do not invent IDs or claim delivery before submission.", "parameters": {"type": "object", "properties": {"fixId": {"type": "string"}, "draftId": {"type": "string"}, "planId": {"type": "string"}}}}},
]


def authorize_tool(name, args, namespace):
    if not isinstance(args, dict):
        raise ValueError("Tool arguments must be structured")
    if name == "fetch_reference" and set(args) == {"url"}:
        validate_url(args["url"])
    elif name == "search_runbooks" and set(args) == {"query"} and isinstance(args["query"], str) and 1 <= len(args["query"]) <= 2000:
        pass
    elif name in {"search_image_tags", "inspect_image_tag"}:
        return authorize_image(args, inspect=name == "inspect_image_tag")
    elif name == "draft_repair":
        return draft_fix(args, namespace, require_authority=False)["draft"]
    elif name == "request_repair_approval" and set(args) in ({"fixId"}, {"draftId"}, {"planId"}) and all(isinstance(v, str) and 1 <= len(v) <= 160 for v in args.values()):
        pass
    else:
        raise ValueError("Invalid investigative tool or arguments")
    return args


def execute(name, args, namespace, tracer=None):
    if name == "search_image_tags": return search_image_tags(**args)
    if name == "inspect_image_tag": return inspect_image_tag(**args)
    if name == "fetch_reference": return fetch_reference(args["url"])
    if name == "search_runbooks": return retrieve(args["query"], tracer=tracer, limit=3)
    if name == "draft_repair": return draft_fix(args, namespace, require_authority=False)
    if name == "request_repair_approval": return {"status": "staged", "selection": args, "note": "Review submission is deferred until output guardrails pass. No change has been executed."}
    raise ValueError("Unknown investigative tool")
