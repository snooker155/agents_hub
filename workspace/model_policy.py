"""
A workspace's model policy: the models it may use, and the settings they run with.

An administrator sets it on a workspace (``PUT /api/workspaces/{name}/model-policy``;
``common.auth.ADMIN_WRITE_SEGMENTS`` keeps it the administrator's in ``multi``
mode, owner or not). Stored in the workspace metadata under ``model_policy``::

    {"models": [{"provider": "openai", "model": "gpt-5"}, ...],
     "default": {"provider": "openai", "model": "gpt-5"},
     "temperature": 0.2, "effort": "medium", "max_tokens": 4096}

With ``models`` empty or the key absent there is no policy. With one, the
workspace runs only those models:

- the default is the policy's (the first model when it names none, so a
  policy of one model makes that model the workspace's default, its only
  choice in the header and the only one an agent may be set to);
- the header's choice and an agent's own model must be on the list; one that
  is not, an agent's pin made before the policy or in another workspace,
  runs on the policy's default instead (``clamp``, agents/agent_factory.py);
- a model switch, a fallback, an advisor or a delegation picks only from the
  list (tools/delegation.py ``enabled_models``/``resolve_model``);
- ``temperature``, ``effort`` and ``max_tokens``, when set, are fixed: they
  beat the agent's own and the header's;
- with ``lock_special_models`` (on unless switched off) the workspace's
  special models (providers/special.py: images, video, speech, transcription,
  its own models) are the administrator's to set too.

A personal workspace (common/personal_workspace.py) with no policy of its own
follows ``default``'s, so a member cannot step around the hub's policy by
working in their own workspace. An administrator can still give a personal
workspace a policy of its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

POLICY_KEY = "model_policy"


@dataclass(frozen=True)
class ModelPolicy:
    models: Tuple[Tuple[str, str], ...]
    default: Tuple[str, str]
    temperature: Optional[float] = None
    effort: Optional[str] = None
    max_tokens: Optional[int] = None
    lock_special_models: bool = True
    #: Set when the policy is another workspace's (``default``'s, for a
    #: personal workspace without its own).
    inherited_from: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def allows(self, provider: Optional[str], model: Optional[str]) -> bool:
        """Whether ``provider``/``model`` is on the list. An empty model is the
        provider's own default one."""
        key = _key(provider, model)
        return key is not None and key in self.models

    def clamp(self, provider: Optional[str], model: Optional[str]) -> Tuple[str, str]:
        """``provider``/``model`` when the policy allows it, else its default."""
        key = _key(provider, model)
        return key if key is not None and key in self.models else self.default

    def model_ids(self) -> List[str]:
        return [f"{p}/{m}" for p, m in self.models]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "models": [{"provider": p, "model": m} for p, m in self.models],
            "default": {"provider": self.default[0], "model": self.default[1]},
            "temperature": self.temperature,
            "effort": self.effort,
            "max_tokens": self.max_tokens,
            "lock_special_models": self.lock_special_models,
            "inherited_from": self.inherited_from,
        }


def _key(provider: Optional[str], model: Optional[str]) -> Optional[Tuple[str, str]]:
    prov = str(provider or "").strip().lower()
    if not prov or prov in ("inherit", "global", "workspace_default"):
        return None
    mdl = str(model or "").strip()
    if not mdl:
        try:
            from agents.agent_utils import default_model_for
            mdl = str(default_model_for(prov) or "").strip()
        except Exception:  # noqa: BLE001 - no default known: nothing to compare
            mdl = ""
    return (prov, mdl) if mdl else None


def _pairs(raw: Any) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str) and "/" in item:
            prov, _, mdl = item.partition("/")
        elif isinstance(item, dict):
            prov, mdl = item.get("provider"), item.get("model")
        else:
            continue
        prov, mdl = str(prov or "").strip().lower(), str(mdl or "").strip()
        if prov and mdl and (prov, mdl) not in out:
            out.append((prov, mdl))
    return out


def normalize(raw: Any) -> Optional[Dict[str, Any]]:
    """The stored shape of a policy, or None for "no policy". Raises
    ``ValueError`` for a default that is not on the list, or a setting out of
    range."""
    if not isinstance(raw, dict):
        return None
    models = _pairs(raw.get("models"))
    if not models:
        return None
    default_raw = raw.get("default")
    default = _pairs([default_raw]) if default_raw else []
    if default and default[0] not in models:
        raise ValueError("the default model must be one of the allowed models")
    policy = ModelPolicy(models=tuple(models), default=default[0] if default else models[0],
                         temperature=_temperature(raw.get("temperature")),
                         effort=_effort(raw.get("effort")),
                         max_tokens=_max_tokens(raw.get("max_tokens")),
                         lock_special_models=raw.get("lock_special_models") is not False)
    stored = policy.to_dict()
    stored.pop("inherited_from", None)
    return stored


def _temperature(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"temperature must be a number, not {value!r}") from exc
    if not 0 <= number <= 2:
        raise ValueError("temperature must be between 0 and 2")
    return number


def _effort(value: Any) -> Optional[str]:
    word = str(value or "").strip().lower()
    if word in ("", "inherit"):
        return None
    from agents.registry import normalize_effort
    effort = normalize_effort(word)
    if effort is None:
        raise ValueError(f"unknown effort '{value}'")
    return effort


def _max_tokens(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"max_tokens must be a whole number, not {value!r}") from exc
    if number < 1:
        raise ValueError("max_tokens must be positive")
    return number


def from_metadata(metadata: Any) -> Optional[ModelPolicy]:
    """The policy a workspace with this metadata runs under: its own, or for
    a personal workspace without one, ``default``'s. None when there is none."""
    own = own_from_metadata(metadata)
    if own is not None or not (isinstance(metadata, dict) and metadata.get("personal_of")):
        return own
    try:
        from workspace import get_workspace_metadata
        fallback = get_workspace_metadata("default")
    except Exception:  # noqa: BLE001 - no default workspace: nothing to follow
        return None
    if not isinstance(fallback, dict) or fallback.get("personal_of"):
        return None
    inherited = own_from_metadata(fallback)
    if inherited is None:
        return None
    from dataclasses import replace
    return replace(inherited, inherited_from="default")


def own_from_metadata(metadata: Any) -> Optional[ModelPolicy]:
    """The policy stored in this workspace's own metadata, or None."""
    raw = metadata.get(POLICY_KEY) if isinstance(metadata, dict) else None
    try:
        stored = normalize(raw)
    except ValueError:
        # A hand-edited policy whose default fell off the list still limits
        # the models; it just defaults to the first.
        stored = normalize({**raw, "default": None}) if isinstance(raw, dict) else None
    if not stored:
        return None
    models = tuple(_pairs(stored["models"]))
    default = _pairs([stored["default"]])[0]
    return ModelPolicy(models=models, default=default, temperature=stored.get("temperature"),
                       effort=stored.get("effort"), max_tokens=stored.get("max_tokens"),
                       lock_special_models=stored.get("lock_special_models") is not False)


def for_workspace(workspace: Optional[str]) -> Optional[ModelPolicy]:
    """The policy of ``workspace`` (a name or an operating path), or None."""
    if not workspace:
        return None
    try:
        from common.workspace_context import workspace_name_from_path
        from workspace import get_workspace_metadata
        name = workspace_name_from_path(workspace) or workspace
        return from_metadata(get_workspace_metadata(name))
    except Exception:  # noqa: BLE001 - an unreadable workspace has no policy to apply
        return None


def special_models_locked(workspace: Optional[str]) -> bool:
    """Whether only an administrator sets ``workspace``'s special models."""
    policy = for_workspace(workspace)
    return policy is not None and policy.lock_special_models


def refusal(workspace: Optional[str], provider: Optional[str], model: Optional[str]) -> Optional[str]:
    """Why ``provider``/``model`` may not be chosen in ``workspace``, or None."""
    policy = for_workspace(workspace)
    if policy is None or policy.allows(provider, model):
        return None
    name = f"{provider}/{model}" if model else str(provider)
    return (f"'{name}' is not allowed in workspace '{workspace}'; allowed: "
            + ", ".join(policy.model_ids()))


__all__ = ["ModelPolicy", "POLICY_KEY", "for_workspace", "from_metadata", "normalize",
           "own_from_metadata", "refusal", "special_models_locked"]
