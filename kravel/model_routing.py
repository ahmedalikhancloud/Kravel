"""Local, deterministic routing. Screening stays on the fast model.

No parallel resident agents or hidden additional cloud/model dependencies.
"""
import re


def changes_requested(question):
    """Diagnosis is not authorization to stage a mutation, even in cluster mode."""
    if re.search(r"\bread[- ]only\b|\b(?:do not|don't|never|no)\s+(?:make\s+|apply\s+)?(?:changes|mutations|writes)\b", question, re.I):
        return False
    question = re.sub(r"\b(?:do not|don't|never)\s+(?:request\s+(?:human\s+)?approval|fix|repair|change|mutate|write)\b", "", question, flags=re.I)
    return bool(re.search(r"\b(?:fix|repair|remediate|create|deploy|configure|build|apply|replace|patch|delete|remove|scale|restart|drain|cordon|uncordon|taint|label|annotate|exec|migrate|update|install|set up)\b|\brequest\s+(?:human\s+)?approval\b", question, re.I))


def select_model(config, question, *, learning=False, override=""):
    fast = config.llm_model
    thinker = getattr(config, "llm_thinking_model", "")
    routing = override or getattr(config, "llm_routing", "fast")
    if routing not in {"fast", "auto", "thinking"}: raise ValueError("Unknown local model route")
    complex_request = len(question) > 320 or bool(re.search(r"\b(?:complex|multi.step|design|architecture|migrate|migration|rbac|clusterrole|networkpolic\w*|custom resource|crd|stateful|storage|drain|dependencies)\b|\b(?:create|deploy|configure|build|set up)\b[\s\S]*\b(?:and|then|with)\b", question, re.I))
    thinking = bool(thinker and not learning and (routing == "thinking" or routing == "auto" and complex_request))
    return {"model": thinker if thinking else fast, "role": "planner" if thinking else "investigator", "reason": "Fast discovery → one thinking planning turn → fast review" if thinking else "Fast path for inspection, learning or simple operations", "routing": routing, "thinking": thinking, "guardrailModel": fast, "sequential": True, "stages": [{"role": "investigator", "model": fast}, *([{"role": "planner", "model": thinker}] if thinking else []), {"role": "coordinator", "model": fast}]}


def route_turn(config, routing, turn, *, learning=False, plan_ready=False):
    """The thinker is a bounded planning specialist, never an endless retry loop."""
    if learning:
        return {"model": config.llm_model, "role": "teacher"}
    if routing["thinking"] and turn == 2 and not plan_ready:
        return {"model": routing["model"], "role": "planner"}
    return {"model": config.llm_model, "role": "investigator" if turn == 1 else "coordinator"}
