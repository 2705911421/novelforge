"""Durable dynamic graph projection for a simulation sandbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from collections.abc import Mapping
from typing import Any

from src.core.database import Database
from src.storyflow.simulation.repository import SimulationRepository


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)


@dataclass(frozen=True, slots=True)
class SimulationGraphProjection:
    run_id: str
    state_hash: str
    event_sequence: int
    nodes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    evidence: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return {"runId": self.run_id, "stateHash": self.state_hash,
                "eventSequence": self.event_sequence, "nodes": list(self.nodes),
                "edges": list(self.edges), "evidence": dict(self.evidence)}


class SimulationGraphProjectionStore:
    """Mutable, rebuildable SQLite read model; the event ledger stays authoritative."""

    # Increment when the projection's node ontology changes so existing runs
    # rebuild their durable read model instead of serving an older cache.
    PROJECTION_VERSION = 3

    def __init__(self, database: Database) -> None:
        self._database = database

    def save(self, projection: SimulationGraphProjection, *, event_limit: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._database.transaction() as conn:
            conn.execute(
                "DELETE FROM simulation_graph_projection_nodes WHERE simulation_run_id=?",
                (projection.run_id,),
            )
            conn.execute(
                "DELETE FROM simulation_graph_projection_edges WHERE simulation_run_id=?",
                (projection.run_id,),
            )
            for node in projection.nodes:
                conn.execute(
                    """INSERT INTO simulation_graph_projection_nodes(
                        simulation_run_id, node_id, node_type, simulation_id, label,
                        payload, event_sequence, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (projection.run_id, str(node.get("id") or ""), str(node.get("type") or "Node"),
                     node.get("simulationId"), str(node.get("label") or node.get("simulationId") or ""),
                     _json(node), projection.event_sequence, now),
                )
            for edge in projection.edges:
                conn.execute(
                    """INSERT INTO simulation_graph_projection_edges(
                        simulation_run_id, edge_id, source, target, edge_type,
                        payload, event_sequence, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (projection.run_id, str(edge.get("id") or ""), str(edge.get("source") or ""),
                     str(edge.get("target") or ""), str(edge.get("type") or "related_to"),
                     _json(edge), projection.event_sequence, now),
                )
            conn.execute(
                """INSERT INTO simulation_graph_projection_meta(
                    simulation_run_id, state_hash, event_sequence, event_limit,
                    projection_version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(simulation_run_id) DO UPDATE SET
                    state_hash=excluded.state_hash, event_sequence=excluded.event_sequence,
                    event_limit=excluded.event_limit, projection_version=excluded.projection_version,
                    updated_at=excluded.updated_at""",
                (projection.run_id, projection.state_hash, projection.event_sequence,
                 event_limit, self.PROJECTION_VERSION, now),
            )

    def load(self, run_id: str) -> SimulationGraphProjection | None:
        meta = self._database.fetchone(
            "SELECT * FROM simulation_graph_projection_meta WHERE simulation_run_id=?",
            (run_id,),
        )
        if meta is None:
            return None
        nodes = tuple(self._payload(row) for row in self._database.fetchall(
            """SELECT payload FROM simulation_graph_projection_nodes
               WHERE simulation_run_id=? ORDER BY node_id""", (run_id,)
        ))
        edges = tuple(self._payload(row) for row in self._database.fetchall(
            """SELECT payload FROM simulation_graph_projection_edges
               WHERE simulation_run_id=? ORDER BY edge_id""", (run_id,)
        ))
        run = self._database.fetchone("SELECT current_round FROM simulation_runs WHERE id=?", (run_id,))
        evidence = {
            "source": "persisted_simulation_graph_projection",
            "canonicalMutation": False,
            "mode": "SIMULATION",
            "runId": run_id,
            "round": int(run["current_round"] if run else 0),
            "nodeCount": len(nodes),
            "edgeCount": len(edges),
            "eventLimit": int(meta["event_limit"]),
            "projectionVersion": int(meta["projection_version"]),
        }
        return SimulationGraphProjection(
            run_id=run_id, state_hash=meta["state_hash"],
            event_sequence=int(meta["event_sequence"]), nodes=nodes, edges=edges,
            evidence=evidence,
        )

    @staticmethod
    def _payload(row: Any) -> dict[str, Any]:
        value = json.loads(row["payload"] or "{}")
        return value if isinstance(value, dict) else {}


class SimulationGraphProjector:
    """Project state and events, caching only a rebuildable sandbox read model."""

    def __init__(self, repository: SimulationRepository, *, store: SimulationGraphProjectionStore | None = None) -> None:
        self._repository = repository
        self._store = store or SimulationGraphProjectionStore(repository.database)

    def project(self, run_id: str, *, event_limit: int = 1000) -> SimulationGraphProjection:
        if event_limit < 1 or event_limit > 5000:
            raise ValueError("event_limit must be between 1 and 5000")
        state = self._repository.recover(run_id)
        cached = self._store.load(run_id)
        if (cached is not None and cached.state_hash == state.state_hash
                and cached.event_sequence == state.event_sequence
                and cached.evidence.get("eventLimit") == event_limit
                and cached.evidence.get("projectionVersion") == self._store.PROJECTION_VERSION):
            return cached
        projection = self._build(run_id, state, event_limit=event_limit)
        self._store.save(projection, event_limit=event_limit)
        return projection

    def _build(self, run_id: str, state: Any, *, event_limit: int) -> SimulationGraphProjection:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        narrative_records: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for collection, entity_type in (("characters", "Character"), ("factions", "Faction"), ("locations", "Location")):
            entities = state.values.get(collection, {})
            if not isinstance(entities, dict):
                continue
            for entity_id in sorted(entities):
                value = entities[entity_id] if isinstance(entities[entity_id], dict) else {}
                nodes.append({"id": f"simulation:{entity_type.lower()}:{entity_id}", "type": entity_type,
                              "label": value.get("name", entity_id), "simulationId": entity_id,
                              "state": {key: value[key] for key in ("location", "territory", "alive", "status", "resources") if key in value}})

        # The graph is a world read model, not an agent-only roster. Expose
        # the other snapshot-backed narrative entities as typed nodes while
        # keeping the canonical Character/Faction/Location ids stable for
        # existing consumers. These nodes remain replayed Sandbox evidence.
        narrative_collections = (
            ("world_rules", "WorldRule", ("rule_text", "category", "id")),
            ("power_systems", "PowerSystem", ("name", "description", "id")),
            ("timeline", "TimelineEvent", ("title", "description", "id")),
            ("foreshadows", "Foreshadow", ("title", "description", "id")),
            ("known_facts", "Fact", ("content", "fact_type", "id")),
            ("plot_threads", "PlotThread", ("title", "name", "id")),
            ("secrets", "Secret", ("title", "name", "id")),
            ("story_goals", "StoryGoal", ("title", "name", "goal", "id")),
            ("conflicts", "Conflict", ("title", "name", "description", "id")),
            ("items", "Item", ("name", "title", "description", "id")),
            ("narrative_obligations", "NarrativeObligation", ("title", "name", "description", "id")),
        )
        for collection, entity_type, label_keys in narrative_collections:
            values = state.values.get(collection, ())
            if isinstance(values, dict):
                iterable = ((key, raw) for key, raw in sorted(values.items(), key=lambda item: str(item[0])))
            elif isinstance(values, (list, tuple)):
                iterable = ((index, raw) for index, raw in enumerate(values))
            else:
                continue
            for index, raw in iterable:
                value = dict(raw) if isinstance(raw, dict) else {"value": raw}
                entity_id = str(value.get("id") or value.get("key") or index)
                label = next((value.get(key) for key in label_keys if value.get(key)), entity_id)
                nodes.append({
                    "id": f"simulation:{entity_type.lower()}:{entity_id}",
                    "type": entity_type,
                    "label": str(label),
                    "simulationId": f"{collection}:{entity_id}",
                    "state": {key: value[key] for key in ("status", "priority", "significance", "resolved_chapter") if key in value},
                })
                narrative_records.setdefault(collection, []).append((
                    f"simulation:{entity_type.lower()}:{entity_id}", value,
                ))

        # Resolve references against the typed node inventory.  ``simulationId``
        # remains the stable external id while graph edges use node ids for new
        # state-derived relationships.  A raw-id fallback keeps legacy event
        # edges readable for clients that already resolve short ids.
        node_by_typed_ref: dict[tuple[str, str], str] = {}
        node_by_raw_ref: dict[str, list[str]] = {}
        for node in nodes:
            node_id = str(node.get("id") or "")
            node_type = self._normalize_type(node.get("type"))
            simulation_id = str(node.get("simulationId") or "")
            if node_id and simulation_id:
                node_by_typed_ref[(node_type, simulation_id)] = node_id
                node_by_raw_ref.setdefault(simulation_id, []).append(node_id)
                if ":" in simulation_id:
                    node_by_raw_ref.setdefault(simulation_id.rsplit(":", 1)[-1], []).append(node_id)

        edge_keys: set[tuple[str, str, str]] = set()

        def node_ref(value: Any, type_hint: Any = None) -> str | None:
            raw = self._reference_id(value)
            if not raw:
                return None
            if raw.startswith("simulation:") and raw in {item.get("id") for item in nodes}:
                return raw
            normalized = self._normalize_type(type_hint)
            if normalized:
                candidate = node_by_typed_ref.get((normalized, raw))
                if candidate:
                    return candidate
            candidates = node_by_raw_ref.get(raw, [])
            return candidates[0] if candidates else None

        def add_edge(edge_id: str, source: Any, target: Any, edge_type: Any, **extra: Any) -> None:
            source_id = str(source or "")
            target_id = str(target or "")
            relation = str(edge_type or "related_to")
            if not source_id or not target_id or source_id == target_id:
                return
            key = (source_id, target_id, relation)
            if key in edge_keys:
                return
            edge_keys.add(key)
            edges.append({"id": edge_id, "source": source_id, "target": target_id,
                          "type": relation, "simulation": True, **extra})

        def add_reference_edges(source: str, values: Any, *, type_hint: Any, edge_type: str,
                                id_prefix: str) -> None:
            for index, item in enumerate(self._reference_values(values)):
                target = node_ref(item, type_hint)
                if target:
                    add_edge(f"simulation:{id_prefix}:{source}:{index}", source, target, edge_type)

        # Character and faction state is the live part of the graph.  These
        # edges are replayed from the Sandbox state and therefore update after
        # every round without writing Canon.
        characters = state.values.get("characters", {})
        if isinstance(characters, Mapping):
            for agent_id, raw in characters.items():
                value = raw if isinstance(raw, Mapping) else {}
                source = node_ref(agent_id, "character")
                if not source:
                    continue
                character_state = value.get("state") if isinstance(value.get("state"), Mapping) else {}
                location = value.get("location") or character_state.get("location")
                target = node_ref(location, "location")
                if target:
                    add_edge(f"simulation:present-at:{agent_id}:{location}", source, target, "present_at")
                relationships = value.get("relationships") or character_state.get("relationships")
                if isinstance(relationships, Mapping):
                    for target_id, relation in sorted(relationships.items(), key=lambda item: str(item[0])):
                        target_node = node_ref(target_id)
                        if target_node:
                            relation_type = str(relation) if isinstance(relation, str) and relation else "related_to"
                            add_edge(f"simulation:agent-relationship:{agent_id}:{target_id}", source,
                                     target_node, relation_type)
                known_facts = value.get("known_facts") or character_state.get("knowledge")
                add_reference_edges(source, known_facts, type_hint="fact", edge_type="knows",
                                    id_prefix="knowledge")

        factions = state.values.get("factions", {})
        if isinstance(factions, Mapping):
            for faction_id, raw in factions.items():
                value = raw if isinstance(raw, Mapping) else {}
                source = node_ref(faction_id, "faction")
                if not source:
                    continue
                faction_state = value.get("state") if isinstance(value.get("state"), Mapping) else {}
                territory = value.get("territory") or faction_state.get("territory")
                add_reference_edges(source, territory, type_hint="location", edge_type="controls",
                                    id_prefix="territory")
                add_reference_edges(source, value.get("allies") or faction_state.get("allies"),
                                    type_hint="faction", edge_type="allies_with", id_prefix="ally")
                add_reference_edges(source, value.get("enemies") or faction_state.get("enemies"),
                                    type_hint="faction", edge_type="hostile_to", id_prefix="enemy")

        locations = state.values.get("locations", {})
        if isinstance(locations, Mapping):
            for location_id, raw in locations.items():
                value = raw if isinstance(raw, Mapping) else {}
                location_node = node_ref(location_id, "location")
                if not location_node:
                    continue
                location_state = value.get("state") if isinstance(value.get("state"), Mapping) else {}
                controller = value.get("controlling_faction") or location_state.get("controlling_faction")
                controller_node = node_ref(controller, "faction")
                if controller_node:
                    add_edge(f"simulation:control:{controller}:{location_id}", controller_node,
                             location_node, "controls")

        # Narrative records carry explicit references in different historical
        # shapes.  Only references that resolve to an inventory node become
        # edges; malformed or name-only values stay visible as node evidence.
        for collection, records in narrative_records.items():
            for source, value in records:
                relation_fields = (
                    ("characters_involved", "character", "involves"),
                    ("character_ids", "character", "involves"),
                    ("characters", "character", "involves"),
                    ("participants", "character", "involves"),
                    ("faction_id", "faction", "involves"),
                    ("faction_ids", "faction", "involves"),
                    ("location", "location", "happens_at"),
                    ("location_id", "location", "happens_at"),
                    ("owner_id", None, "owned_by"),
                    ("character_id", "character", "involves"),
                    ("fact_ids", "fact", "references"),
                    ("known_facts", "fact", "references"),
                )
                for field, type_hint, edge_type in relation_fields:
                    if field in value:
                        add_reference_edges(source, value[field], type_hint=type_hint,
                                            edge_type=edge_type, id_prefix=f"{collection}-{field}")

        # Entity knowledge is intentionally projected as edges from the Agent
        # to fact nodes; the underlying knowledge scope remains in the sandbox
        # and is never broadened by this read model.
        entity_knowledge = state.values.get("entity_knowledge", {})
        if isinstance(entity_knowledge, Mapping):
            for agent_id, raw in entity_knowledge.items():
                source = node_ref(agent_id)
                if not source or not isinstance(raw, Mapping):
                    continue
                for field, edge_type in (("known_facts", "knows"), ("facts", "knows"),
                                         ("beliefs", "believes"), ("suspects", "suspects"),
                                         ("heard_rumors", "heard_rumor")):
                    if field in raw:
                        add_reference_edges(source, raw[field], type_hint="fact", edge_type=edge_type,
                                            id_prefix=f"entity-knowledge-{field}")

        relationships = state.values.get("relationships", [])
        if isinstance(relationships, (list, tuple)):
            for relation in relationships:
                if not isinstance(relation, dict):
                    continue
                source = relation.get("source_id") or relation.get("source")
                target = relation.get("target_id") or relation.get("target")
                if source and target:
                    edges.append({"id": f"simulation:relationship:{relation.get('id', len(edges))}",
                                  "source": str(source), "target": str(target),
                                  "type": relation.get("relationship_type", "related_to"),
                                  "simulation": True,
                                  "sourceNodeId": node_ref(source, relation.get("source_type")),
                                  "targetNodeId": node_ref(target, relation.get("target_type"))})
        for event in self._repository.events(run_id)[-event_limit:]:
            if not event.actor_id:
                continue
            source = f"simulation:{(event.actor_type or 'character').lower()}:{event.actor_id}"
            for target in event.target_ids:
                edges.append({"id": f"simulation:event:{event.id}:{target}", "source": source,
                              "target": str(target), "type": event.event_type, "sequence": event.sequence,
                              "simulation": True, "targetNodeId": node_ref(target)})
        return SimulationGraphProjection(
            run_id=run_id, state_hash=state.state_hash, event_sequence=state.event_sequence,
            nodes=tuple(nodes), edges=tuple(edges),
            evidence={"source": "persisted_simulation_graph_projection", "canonicalMutation": False,
                      "mode": "SIMULATION", "runId": run_id, "round": self._repository.get_run(run_id).current_round,
                      "nodeCount": len(nodes), "edgeCount": len(edges), "eventLimit": event_limit,
                      "projectionVersion": SimulationGraphProjectionStore.PROJECTION_VERSION},
        )

    @staticmethod
    def _normalize_type(value: Any) -> str:
        normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "characters": "character", "character_agent": "character", "characteragent": "character",
            "factions": "faction", "faction_agent": "faction", "factionagent": "faction",
            "locations": "location", "timeline_events": "timelineevent", "timeline_event": "timelineevent",
            "world_rules": "worldrule", "world_rule": "worldrule", "power_systems": "powersystem",
            "power_system": "powersystem", "known_facts": "fact", "story_facts": "fact",
            "plot_threads": "plotthread", "plot_thread": "plotthread", "story_goals": "storygoal",
            "story_goal": "storygoal", "narrative_obligations": "narrativeobligation",
            "narrative_obligation": "narrativeobligation",
        }
        return aliases.get(normalized, normalized)

    @staticmethod
    def _reference_id(value: Any) -> str | None:
        if isinstance(value, Mapping):
            for key in (
                "id", "entity_id", "entityId", "agent_id", "agentId", "character_id", "characterId",
                "faction_id", "factionId", "location_id", "locationId", "fact_id", "factId",
            ):
                candidate = value.get(key)
                if candidate not in (None, ""):
                    return str(candidate)
            return None
        if value in (None, ""):
            return None
        return str(value)

    @classmethod
    def _reference_values(cls, value: Any) -> tuple[Any, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return (value,)
            if decoded == value:
                return (value,)
            return cls._reference_values(decoded)
        if isinstance(value, Mapping):
            # Relationship/knowledge maps use keys as stable entity ids;
            # object-shaped references use one of their explicit id fields.
            explicit = cls._reference_id(value)
            if explicit is not None:
                return (explicit,)
            return tuple(value.keys())
        if isinstance(value, (list, tuple, set)):
            return tuple(item for item in value if cls._reference_id(item) is not None)
        return (value,)
