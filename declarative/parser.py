"""Reading declarative files into a :class:`Bundle`.

Two file shapes:

* **Markdown** (``*.md``): one agent. YAML frontmatter between ``---`` lines
  carries the structured fields, the body is ``instructions.md``. Its
  ``capabilities.md`` and ``usage.md`` come from frontmatter text, from a
  ``capabilities_file:`` / ``usage_file:`` path, or from sibling files
  (``<name>.capabilities.md`` next to ``<name>.md``, or ``capabilities.md``
  next to an ``agent.md`` / ``instructions.md``).
* **YAML** (``*.yaml``, ``*.yml``): one or more documents separated by
  ``---``, each a mapping with ``kind:``.

Every resource needs an ``id``: its key in the bundle and the lock. For an
agent it is also the hub id; the other kinds get a hub id on creation and the
lock maps one to the other.

Nothing here talks to a hub. Problems are collected across every file and
raised together as one :class:`ValidationError` of ``file:line: message``
lines, so a user fixes them in one pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from declarative.errors import Problem, ValidationError

#: Folders a directory scan never enters.
SKIP_DIRS = frozenset({"node_modules", "__pycache__", ".git", ".venv", "venv"})
#: Markdown names that hold an agent whose sibling capabilities.md / usage.md
#: belong to it.
FOLDER_AGENT_NAMES = ("agent.md", "instructions.md")
#: The markdown parts of an agent besides its instructions.
EXTRA_PARTS = ("capabilities", "usage")

PathLike = Union[str, Path]


@dataclass
class Resource:
    """One declared resource."""
    kind: str
    key: str
    spec: Dict[str, Any]
    source: str
    line: Optional[int] = None
    #: Line of each top-level field, for messages that point at one.
    field_lines: Dict[str, int] = field(default_factory=dict)

    @property
    def address(self) -> str:
        return f"{self.kind}/{self.key}"

    def where(self, name: Optional[str] = None) -> Tuple[str, Optional[int]]:
        return self.source, self.field_lines.get(name, self.line) if name else self.line

    def problem(self, message: str, name: Optional[str] = None) -> Problem:
        source, line = self.where(name)
        return Problem(source, line, message)


@dataclass
class Bundle:
    """Every resource read from the given paths, in file order."""
    resources: List[Resource] = field(default_factory=list)
    #: The folder the first path names (the default home of the lock file).
    root: Optional[Path] = None
    files: List[str] = field(default_factory=list)

    def get(self, kind: str, key: str) -> Optional[Resource]:
        for res in self.resources:
            if res.kind == kind and res.key == key:
                return res
        return None

    def by_kind(self, kind: str) -> List[Resource]:
        return [r for r in self.resources if r.kind == kind]

    def addresses(self) -> List[str]:
        return [r.address for r in self.resources]

    def __iter__(self):
        return iter(self.resources)

    def __len__(self) -> int:
        return len(self.resources)


# ---------------------------------------------------------------------------
# YAML with line numbers
# ---------------------------------------------------------------------------


class _Mapping(dict):
    """A YAML mapping that remembers where it and each of its keys started."""
    line: int = 0
    lines: Dict[Any, int]


def _yaml():
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - pyyaml ships with the hub
        raise ValidationError([Problem("<install>", None,
                                       "reading declarative files needs PyYAML: pip install pyyaml")]) from exc
    return yaml


def _loader_class():
    yaml = _yaml()

    class LineLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader, node, deep=False):
        loader.flatten_mapping(node)
        out = _Mapping()
        out.line = node.start_mark.line + 1
        out.lines = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=True)
            if key in out:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key '{key}'", key_node.start_mark)
            out[key] = loader.construct_object(value_node, deep=True)
            out.lines[key] = key_node.start_mark.line + 1
        return out

    LineLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)
    return LineLoader


def plain(value: Any) -> Any:
    """A parsed YAML value as plain JSON-able data: dict subclasses become
    dicts, dates become ISO strings."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _yaml_documents(text: str, source: str, line_offset: int,
                    problems: List[Problem]) -> List[Any]:
    yaml = _yaml()
    try:
        return list(yaml.load_all(text, Loader=_loader_class()))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = (mark.line + 1 + line_offset) if mark is not None else None
        reason = getattr(exc, "problem", None) or str(exc)
        problems.append(Problem(source, line, f"invalid YAML: {reason}"))
        return []


def _resource(doc: Any, source: str, line_offset: int, problems: List[Problem],
              default_kind: Optional[str] = None) -> Optional[Resource]:
    if not isinstance(doc, dict):
        problems.append(Problem(source, (getattr(doc, "line", 0) or 0) + line_offset or None,
                                "a document must be a mapping of fields"))
        return None
    lines = {str(k): v + line_offset for k, v in getattr(doc, "lines", {}).items()}
    start = (getattr(doc, "line", 0) or 0) + line_offset or None
    kind = doc.get("kind", default_kind)
    if not kind:
        problems.append(Problem(source, start, "no kind: (agent, environment, deployment or memory_pool)"))
        return None
    raw_id = doc.get("id")
    if raw_id in (None, ""):
        problems.append(Problem(source, lines.get("kind", start), f"{kind} has no id"))
        return None
    spec = {k: v for k, v in plain(doc).items() if k not in ("kind", "id")}
    return Resource(kind=str(kind).strip(), key=str(raw_id).strip(), spec=spec,
                    source=source, line=lines.get("id", start), field_lines=lines)


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def _display(path: Path, base: Optional[Path]) -> str:
    if base is not None:
        try:
            return str(path.resolve().relative_to(base.resolve()))
        except ValueError:
            pass
    return str(path)


def split_frontmatter(text: str) -> Optional[Tuple[str, str, int]]:
    """``(frontmatter, body, body_start_line)`` of a markdown file, or None
    when it has no frontmatter."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:]), i + 2
    return None


def _sibling_parts(path: Path) -> Dict[str, Path]:
    found: Dict[str, Path] = {}
    for part in EXTRA_PARTS:
        if path.name in FOLDER_AGENT_NAMES:
            candidate = path.with_name(f"{part}.md")
        else:
            candidate = path.with_name(f"{path.stem}.{part}.md")
        if candidate.is_file():
            found[part] = candidate
    return found


def _read_markdown(path: Path, source: str, problems: List[Problem], *, explicit: bool) -> List[Resource]:
    text = path.read_text(encoding="utf-8")
    split = split_frontmatter(text)
    if split is None:
        if explicit:
            problems.append(Problem(source, 1, "a markdown agent file starts with --- frontmatter"))
        return []
    front, body, _body_line = split
    docs = _yaml_documents(front, source, 1, problems)
    if not docs:
        return []
    res = _resource(docs[0], source, 1, problems, default_kind="agent")
    if res is None:
        return []
    if res.kind != "agent":
        problems.append(res.problem(f"a markdown file declares an agent, not a {res.kind}", "kind"))
        return []
    if "instructions" in res.spec:
        problems.append(res.problem("the instructions are the body of the file, not a field", "instructions"))
    res.spec["instructions"] = body
    siblings = _sibling_parts(path)
    for part in EXTRA_PARTS:
        ref = res.spec.pop(f"{part}_file", None)
        sources = [s for s in (part in res.spec, ref is not None) if s]
        if len(sources) > 1:
            problems.append(res.problem(f"{part} given both inline and as {part}_file", f"{part}_file"))
            continue
        if ref is not None:
            target = (path.parent / str(ref)).resolve()
            if not target.is_file():
                problems.append(res.problem(f"{part}_file '{ref}' does not exist", f"{part}_file"))
                continue
            res.spec[part] = target.read_text(encoding="utf-8")
        elif part not in res.spec and part in siblings:
            res.spec[part] = siblings[part].read_text(encoding="utf-8")
    return [res]


def _read_yaml(path: Path, source: str, problems: List[Problem], *, explicit: bool) -> List[Resource]:
    docs = [d for d in _yaml_documents(path.read_text(encoding="utf-8"), source, 0, problems)
            if d is not None]
    if not explicit and docs and not any(isinstance(d, dict) and "kind" in d for d in docs):
        return []  # some other YAML in the repository (a CI config, a compose file)
    out: List[Resource] = []
    for doc in docs:
        res = _resource(doc, source, 0, problems)
        if res is not None:
            out.append(res)
    return out


def _is_part_file(path: Path) -> bool:
    """A capabilities/usage file that belongs to an agent next to it."""
    name = path.name
    if any(name.endswith(f".{part}.md") for part in EXTRA_PARTS):
        return True
    if name in (f"{part}.md" for part in EXTRA_PARTS):
        return any((path.parent / n).is_file() for n in FOLDER_AGENT_NAMES)
    return False


def _scan(folder: Path) -> List[Path]:
    out: List[Path] = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if any(part.startswith(".") or part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix in (".yaml", ".yml") or (path.suffix == ".md" and not _is_part_file(path)):
            out.append(path)
    return out


def load_bundle(paths: Union[PathLike, Sequence[PathLike]], *,
                base: Optional[PathLike] = None) -> Bundle:
    """Read every declarative file under ``paths`` (files or folders) into a
    bundle, validated. Raises :class:`ValidationError` listing every problem;
    a bundle that comes back is safe to plan."""
    from declarative import kinds

    items = [paths] if isinstance(paths, (str, Path)) else list(paths)
    if not items:
        raise ValidationError([Problem("<paths>", None, "no files or folders to apply")])
    first = Path(items[0])
    root = first if first.is_dir() else first.parent
    display_base = Path(base) if base is not None else (root if len(items) == 1 else None)

    problems: List[Problem] = []
    bundle = Bundle(root=root)
    for item in items:
        p = Path(item)
        if not p.exists():
            problems.append(Problem(str(p), None, "no such file or folder"))
            continue
        files = _scan(p) if p.is_dir() else [p]
        for f in files:
            source = _display(f, display_base)
            explicit = not p.is_dir()
            if f.suffix == ".md":
                found = _read_markdown(f, source, problems, explicit=explicit)
            elif f.suffix in (".yaml", ".yml"):
                found = _read_yaml(f, source, problems, explicit=explicit)
            else:
                problems.append(Problem(source, None, "not a .md, .yaml or .yml file"))
                continue
            if found:
                bundle.files.append(source)
            bundle.resources.extend(found)

    problems.extend(validate(bundle, kinds))
    if problems:
        raise ValidationError(problems)
    return bundle


def load_text(text: str, *, source: str = "<text>", markdown: bool = False) -> Bundle:
    """A bundle from one file's text, for a caller holding it in memory."""
    from declarative import kinds

    problems: List[Problem] = []
    if markdown:
        split = split_frontmatter(text)
        resources: List[Resource] = []
        if split is None:
            problems.append(Problem(source, 1, "a markdown agent file starts with --- frontmatter"))
        else:
            docs = _yaml_documents(split[0], source, 1, problems)
            res = _resource(docs[0], source, 1, problems, default_kind="agent") if docs else None
            if res is not None:
                res.spec["instructions"] = split[1]
                resources.append(res)
    else:
        resources = [r for r in (_resource(d, source, 0, problems)
                                 for d in _yaml_documents(text, source, 0, problems) if d is not None)
                     if r is not None]
    bundle = Bundle(resources=resources, files=[source])
    problems.extend(validate(bundle, kinds))
    if problems:
        raise ValidationError(problems)
    return bundle


def validate(bundle: Bundle, kinds_module: Any = None) -> List[Problem]:
    """Kind, field and duplicate checks over a whole bundle. References to a
    resource that is not declared are left to the plan, which asks the hub."""
    if kinds_module is None:
        from declarative import kinds as kinds_module
    problems: List[Problem] = []
    seen: Dict[str, Resource] = {}
    for res in bundle.resources:
        handler = kinds_module.KINDS.get(res.kind)
        if handler is None:
            known = ", ".join(sorted(kinds_module.KINDS))
            problems.append(res.problem(f"unknown kind '{res.kind}' (known: {known})", "kind"))
            continue
        if res.address in seen:
            first = seen[res.address]
            problems.append(res.problem(
                f"{res.kind} '{res.key}' is declared twice (first at {first.source}:{first.line})", "id"))
            continue
        seen[res.address] = res
        problems.extend(handler.validate(res))
    for res in bundle.resources:
        handler = kinds_module.KINDS.get(res.kind)
        if handler is None or res.address not in seen or seen[res.address] is not res:
            continue
        for name, ref_kind, value in handler.refs(res):
            if value in (None, ""):
                problems.append(res.problem(f"{name}: an empty reference", name))
    return problems


__all__ = ["Resource", "Bundle", "load_bundle", "load_text", "validate", "split_frontmatter", "plain"]
