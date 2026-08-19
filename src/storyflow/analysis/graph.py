"""Durable dynamic graph projection for a simulation sandbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
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

    PROJECTION_VERSION = 1

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
        evidence = {
            "source": "persisted_simulation_graph_projection",
            "canonicalMutation": False,
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
                and cached.evidence.get("eventLimit") == event_limit):
            return cached
        projection = self._build(run_id, state, event_limit=event_limit)
        self._store.save(projection, event_limit=event_limit)
        return projection

    def _build(self, run_id: str, state: Any, *, event_limit: int) -> SimulationGraphProjection:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        for collection, entity_type in (("characters", "Character"), ("factions", "Faction"), ("locations", "Location")):
            entities = state.values.get(collection, {})
            if not isinstance(entities, dict):
                continue
            for entity_id in sorted(entities):
                value = entities[entity_id] if isinstance(entities[entity_id], dict) else {}
                nodes.append({"id": f"simulation:{entity_type.lower()}:{entity_id}", "type": entity_type,
                              "label": value.get("name", entity_id), "simulationId": entity_id,
                              "state": {key: value[key] for key in ("location", "territory", "alive", "status", "resources") if key in value}})
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
                                  "simulation": True})
        for event in self._repository.events(run_id)[-event_limit:]:
            if not event.actor_id:
                continue
            source = f"simulation:{(event.actor_type or 'character').lower()}:{event.actor_id}"
            for target in event.target_ids:
                edges.append({"id": f"simulation:event:{event.id}:{target}", "source": source,
                              "target": str(target), "type": event.event_type, "sequence": event.sequence,
                              "simulation": True})
        return SimulationGraphProjection(
            run_id=run_id, state_hash=state.state_hash, event_sequence=state.event_sequence,
            nodes=tuple(nodes), edges=tuple(edges),
            evidence={"source": "persisted_simulation_graph_projection", "canonicalMutation": False,
                      "nodeCount": len(nodes), "edgeCount": len(edges), "eventLimit": event_limit,
                      "projectionVersion": SimulationGraphProjectionStore.PROJECTION_VERSION},
        )
