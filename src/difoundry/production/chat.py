from __future__ import annotations

import re
from typing import Any


class ChatPlanner:
    """Provider-neutral chat planning boundary with a deterministic safe fallback."""

    CONNECT_RE = re.compile(r"(?:connect|link|sync)\s+(.+?)\s+(?:to|with|and)\s+(.+?)(?:\s+(?:so|to|when)\s+(.+))?$", re.I)

    def respond(self, content: str, draft: dict[str, Any], systems: list[dict[str, Any]], attached_system_ids: list[str]) -> tuple[str, dict[str, Any], str | None]:
        updated = dict(draft)
        by_id = {item["system_id"]: item for item in systems}
        attached = [by_id[item] for item in attached_system_ids if item in by_id]
        if attached:
            updated.setdefault("system_ids", [])
            updated["system_ids"] = list(dict.fromkeys([*updated["system_ids"], *[item["system_id"] for item in attached]]))
            if len(updated["system_ids"]) >= 2:
                updated.setdefault("source_system_id", updated["system_ids"][0])
                updated.setdefault("target_system_ids", updated["system_ids"][1:])

        match = self.CONNECT_RE.search(content.strip())
        if match:
            source_name, target_name, goal = match.groups()
            source = self._match_system(source_name, systems)
            target = self._match_system(target_name, systems)
            if source:
                updated["source_system_id"] = source["system_id"]
            else:
                updated["source_name"] = source_name.strip(" .")
            if target:
                updated["target_system_ids"] = [target["system_id"]]
            else:
                updated["target_names"] = [target_name.strip(" .")]
            if goal:
                updated["goal"] = goal.strip()

        lower = content.lower()
        if "when " in lower and "goal" not in updated:
            updated["goal"] = content.strip()
        elif len(content.split()) > 7 and "goal" not in updated:
            updated["goal"] = content.strip()

        build_requested = any(phrase in lower for phrase in ("build it", "create it", "deploy it", "go ahead", "/compose"))
        missing: list[str] = []
        if not updated.get("source_system_id"):
            missing.append("the source system")
        if not updated.get("target_system_ids"):
            missing.append("at least one target system")
        if not updated.get("goal"):
            missing.append("what should happen between them")

        if build_requested and not missing:
            return (
                "I have enough information to create the integration draft. I queued discovery, semantic mapping, contract composition, and verification. The connection will remain review-gated if any business meaning is ambiguous.",
                updated,
                "compose",
            )
        if missing:
            registered = ", ".join(item["name"] for item in systems[:8]) or "none yet"
            return (
                f"I can build this, but I still need {', '.join(missing)}. Registered systems: {registered}. Attach systems to this chat or add their API/schema descriptions in Systems.",
                updated,
                None,
            )
        return (
            "I have the source, targets, and desired behavior. Say “build it” to generate the daughter, or add trigger, conflict, permission, and approval constraints first.",
            updated,
            None,
        )

    @staticmethod
    def _match_system(name: str, systems: list[dict[str, Any]]) -> dict[str, Any] | None:
        needle = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
        exact = [item for item in systems if re.sub(r"[^a-z0-9]+", " ", item["name"].lower()).strip() == needle]
        if len(exact) == 1:
            return exact[0]
        contains = [item for item in systems if needle in item["name"].lower() or item["name"].lower() in needle]
        return contains[0] if len(contains) == 1 else None
