"""Observable build plans for ``asmpython build --graphonly``."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .capability_negotiation import negotiate_build
from .embedded_data import collect_files
from .fast_state import prepare_state, state_summary


class BuildPlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlanNode:
    id: str
    kind: str
    label: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class PlanEdge:
    source: str
    target: str
    relation: str

    def as_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
        }


@dataclass
class BuildPlan:
    source: Path
    nodes: list[PlanNode]
    edges: list[PlanEdge]
    negotiation: dict[str, Any]
    fastcomp: dict[str, Any] | None
    embedded: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": "asmpython.build-plan",
            "format_version": 1,
            "source": str(self.source),
            "nodes": [node.as_dict() for node in self.nodes],
            "edges": [edge.as_dict() for edge in self.edges],
            "negotiation": self.negotiation,
            "fastcomp": self.fastcomp,
            "embedded": self.embedded,
        }


def _option_values(argv: list[str], flag: str) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == flag and index + 1 < len(argv):
            result.append(argv[index + 1])
            index += 2
            continue
        if token.startswith(flag + "="):
            result.append(token.split("=", 1)[1])
        index += 1
    return result


def _source_from_argv(argv: list[str]) -> Path:
    tokens = list(argv)
    if tokens and tokens[0] == "build":
        tokens = tokens[1:]
    value_flags = {
        "--backend", "--linker", "--target", "--type", "--output", "-o",
        "--profile", "--sanitize", "--report", "--debug-format", "--embed",
        "--lockfile", "--config", "--graph-format", "--graph-output",
    }
    skip = False
    for token in tokens:
        if skip:
            skip = False
            continue
        if token in value_flags:
            skip = True
            continue
        if token.startswith("-"):
            continue
        path = Path(token)
        if path.suffix in {".py", ".json", ".toml", ".apir"} or path.exists():
            return path.resolve()
    raise BuildPlanError("build plan requires a source or build.config.toml entry")


def create_build_plan(argv: list[str]) -> BuildPlan:
    source = _source_from_argv(argv)
    negotiation = negotiate_build(argv)
    backend = negotiation.backend.name
    target = negotiation.target
    fastcomp_enabled = "--fastcomp" in argv
    fast = None
    state = None
    if source.suffix == ".py":
        state = prepare_state(source, backend=backend, target=target)
        if fastcomp_enabled:
            fast = state_summary(state)

    nodes: list[PlanNode] = [
        PlanNode("source", "source", source.name, {"path": str(source)}),
        PlanNode("discover", "stage", "source and dependency discovery"),
        PlanNode("parse", "stage", "parse modules"),
        PlanNode("analyze", "stage", "semantic analysis"),
        PlanNode("partition", "stage", "native / PyinBin partition"),
        PlanNode("ir", "stage", "typed and optimized IR"),
        PlanNode("backend", "backend", backend, negotiation.backend.as_dict()),
    ]
    if negotiation.linker is not None:
        nodes.append(
            PlanNode(
                "linker",
                "linker",
                negotiation.linker.name,
                negotiation.linker.as_dict(),
            )
        )
    nodes.extend(
        [
            PlanNode("bundle", "stage", "bundle runtime and resources"),
            PlanNode("verify", "stage", "artifact verification"),
            PlanNode("output", "artifact", "final output"),
        ]
    )

    edges = [
        PlanEdge("source", "discover", "input"),
        PlanEdge("discover", "parse", "dependencies"),
        PlanEdge("parse", "analyze", "AST"),
        PlanEdge("analyze", "partition", "typed program"),
        PlanEdge("partition", "ir", "native regions"),
        PlanEdge("ir", "backend", "lower"),
    ]
    previous = "backend"
    if negotiation.linker is not None:
        edges.append(PlanEdge("backend", "linker", "objects"))
        previous = "linker"
    edges.extend(
        [
            PlanEdge(previous, "bundle", "binary"),
            PlanEdge("bundle", "verify", "candidate artifact"),
            PlanEdge("verify", "output", "atomic publish"),
        ]
    )

    if state is not None:
        for index, (path, dependencies) in enumerate(sorted(state.graph.items())):
            node_id = f"module:{index}"
            nodes.append(
                PlanNode(
                    node_id,
                    "module",
                    Path(path).name,
                    {"path": path, "sha256": state.dependencies.get(path)},
                )
            )
            edges.append(PlanEdge("discover", node_id, "found"))
        path_to_id = {
            node.metadata.get("path"): node.id
            for node in nodes
            if node.kind == "module"
        }
        for path, dependencies in state.graph.items():
            source_id = path_to_id.get(path)
            if source_id is None:
                continue
            for dependency in dependencies:
                target_id = path_to_id.get(dependency)
                if target_id is not None:
                    edges.append(PlanEdge(source_id, target_id, "imports"))

    embedded_records: list[dict[str, Any]] = []
    embed_paths = [Path(value) for value in _option_values(argv, "--embed")]
    if embed_paths:
        files = collect_files(embed_paths)
        for index, (name, content) in enumerate(files.items()):
            node_id = f"embedded:{index}"
            nodes.append(PlanNode(node_id, "resource", name, {"bytes": len(content)}))
            edges.append(PlanEdge(node_id, "bundle", "embed"))
            embedded_records.append({"name": name, "bytes": len(content)})

    return BuildPlan(
        source=source,
        nodes=nodes,
        edges=edges,
        negotiation=negotiation.as_dict(),
        fastcomp=fast,
        embedded=embedded_records,
    )


def render_text(plan: BuildPlan) -> str:
    lines = [f"Build plan: {plan.source}"]
    for node in plan.nodes:
        lines.append(f"[{node.kind:<9}] {node.id:<18} {node.label}")
    lines.append("")
    lines.append("Edges:")
    for edge in plan.edges:
        lines.append(f"  {edge.source} --{edge.relation}--> {edge.target}")
    if plan.negotiation.get("errors"):
        lines.append("")
        lines.append("Negotiation errors:")
        lines.extend(f"  - {item}" for item in plan.negotiation["errors"])
    return "\n".join(lines)


def render_dot(plan: BuildPlan) -> str:
    lines = ["digraph asmpython_build {"]
    for node in plan.nodes:
        label = node.label.replace('"', '\\"')
        lines.append(f'  "{node.id}" [label="{label}\\n({node.kind})"];')
    for edge in plan.edges:
        relation = edge.relation.replace('"', '\\"')
        lines.append(
            f'  "{edge.source}" -> "{edge.target}" [label="{relation}"];'
        )
    lines.append("}")
    return "\n".join(lines)


def graphonly_main(argv: list[str]) -> int:
    raw: list[str] = []
    format_name = "text"
    output: Path | None = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--graphonly":
            index += 1
            continue
        if token == "--graph-format":
            if index + 1 >= len(argv):
                raise BuildPlanError("--graph-format requires text, json, or dot")
            format_name = argv[index + 1]
            index += 2
            continue
        if token.startswith("--graph-format="):
            format_name = token.split("=", 1)[1]
            index += 1
            continue
        if token == "--graph-output":
            if index + 1 >= len(argv):
                raise BuildPlanError("--graph-output requires a path")
            output = Path(argv[index + 1])
            index += 2
            continue
        if token.startswith("--graph-output="):
            output = Path(token.split("=", 1)[1])
            index += 1
            continue
        raw.append(token)
        index += 1
    if format_name not in {"text", "json", "dot"}:
        raise BuildPlanError("--graph-format must be text, json, or dot")
    plan = create_build_plan(raw)
    if format_name == "json":
        rendered = json.dumps(plan.as_dict(), indent=2, sort_keys=True, ensure_ascii=False)
    elif format_name == "dot":
        rendered = render_dot(plan)
    else:
        rendered = render_text(plan)
    if output is None:
        print(rendered)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(rendered + "\n", encoding="utf-8")
        temporary.replace(output)
        print(f"asmpython: wrote build graph {output}")
    return 0 if plan.negotiation.get("compatible", False) else 1


__all__ = [
    "BuildPlan",
    "BuildPlanError",
    "create_build_plan",
    "graphonly_main",
    "render_dot",
    "render_text",
]
