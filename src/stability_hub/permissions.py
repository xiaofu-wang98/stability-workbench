"""Application-injected actors; request payloads never grant their own permissions."""

from enum import Enum

from .models import Model, Text


class Capability(str, Enum):
    VIEW = "view"
    PREVIEW = "preview"
    CONFIRM_FACT = "confirm_fact"
    ADOPT_RESULT = "adopt_result"
    CORRECT_RESULT = "correct_result"
    REVIEW_DUPLICATE = "review_duplicate"
    EXPORT = "export"
    MANAGE = "manage"


class Role(str, Enum):
    TECHNICIAN = "technician"
    SENIOR_TECHNICIAN = "senior_technician"
    PROJECT_LEAD = "project_lead"
    ADMIN = "admin"


ROLE_CAPABILITIES = {
    Role.TECHNICIAN: frozenset({Capability.VIEW, Capability.PREVIEW, Capability.CONFIRM_FACT}),
    Role.SENIOR_TECHNICIAN: frozenset({
        Capability.VIEW, Capability.PREVIEW, Capability.CONFIRM_FACT,
        Capability.REVIEW_DUPLICATE, Capability.ADOPT_RESULT, Capability.CORRECT_RESULT,
    }),
    Role.PROJECT_LEAD: frozenset({Capability.VIEW, Capability.EXPORT}),
    Role.ADMIN: frozenset(Capability),
}


class Actor(Model):
    id: Text
    display_name: Text
    capabilities: frozenset[Capability]
    role: Role | None = None


def actor_for_role(actor_id: str, display_name: str, role: Role) -> Actor:
    return Actor(id=actor_id, display_name=display_name, role=role, capabilities=ROLE_CAPABILITIES[role])


def require(actor: Actor, capability: Capability) -> None:
    if capability not in actor.capabilities:
        raise PermissionError(f"Actor {actor.id} lacks {capability.value}")
