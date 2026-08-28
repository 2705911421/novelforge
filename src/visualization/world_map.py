"""HTML/SVG world-map fallback for books without an image-generation model."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Optional

from src.core.database import Database


def _json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return []
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []


class WorldMapGenerator:
    """Build a navigable map from persisted locations and their connections.

    This is deliberately independent of image generation.  It gives authors a
    usable structural map immediately, while still allowing a later visual
    asset to be generated and attached to the same world data.
    """

    def __init__(self, db: Database):
        self.db = db

    def generate_html(self, book_id: str, output_path: str, *, title: Optional[str] = None) -> str:
        graph = self._build_graph(book_id)
        book = self.db.fetchone("SELECT title FROM books WHERE id=?", (book_id,)) or {}
        book_title = title or book.get("title") or book_id
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._render_html(book_title, graph), encoding="utf-8")
        return str(path)

    def _build_graph(self, book_id: str) -> dict[str, Any]:
        rows = self.db.fetchall(
            "SELECT id, parent_id, name, description, type, significance "
            "FROM locations WHERE book_id=? ORDER BY rowid",
            (book_id,),
        )
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        node_by_id: dict[str, dict[str, Any]] = {}
        children: dict[Optional[str], list[dict[str, Any]]] = {}
        for row in rows:
            node = {
                "id": row["id"],
                "name": row.get("name") or "未命名地点",
                "type": row.get("type") or "地点",
                "description": row.get("description") or "",
                "significance": row.get("significance") or "",
                "parentId": row.get("parent_id"),
            }
            node_by_id[node["id"]] = node
            children.setdefault(node["parentId"], []).append(node)

        if not rows:
            return {"nodes": [], "edges": [], "layoutWarnings": []}

        # A stable tree layout makes the map useful on first open and remains
        # readable when an author has hundreds of locations.  ``parent_id`` is
        # legacy, user-editable data and does not have a database-level cycle
        # constraint, so layout must treat a cycle as a broken visual edge
        # instead of recursing forever.
        x_step = 250
        y_step = 145
        next_x = 0
        layout_warnings: list[dict[str, str]] = []
        warning_keys: set[tuple[str, str, str]] = set()

        def warn(code: str, node_id: str, parent_id: str) -> None:
            key = (code, node_id, parent_id)
            if key not in warning_keys:
                warning_keys.add(key)
                layout_warnings.append({"code": code, "nodeId": node_id, "parentId": parent_id})

        roots = children.get(None, []) + children.get("", [])
        if not roots:
            roots = [node_by_id[str(rows[0]["id"])]]
        root_ids = {node["id"] for node in roots}
        for row in rows:
            if row["id"] not in root_ids and row.get("parent_id") not in node_by_id:
                roots.append(node_by_id[row["id"]])
                root_ids.add(row["id"])

        positioned: set[str] = set()
        raw_positions: dict[str, float] = {}

        def assign(node: dict[str, Any], depth: int, path: set[str]) -> float:
            nonlocal next_x
            node_id = str(node["id"])
            if node_id in positioned:
                return raw_positions[node_id]
            path.add(node_id)
            child_rows = children.get(node["id"], [])
            positions: list[float] = []
            for child in child_rows:
                child_id = str(child["id"])
                if child_id in path:
                    warn("LOCATION_HIERARCHY_CYCLE", child_id, node_id)
                    continue
                positions.append(assign(child, depth + 1, path))
            path.remove(node_id)
            if positions:
                x = (positions[0] + positions[-1]) / 2
            else:
                x = next_x * x_step
                next_x += 1
            node["x"] = x + 180
            node["y"] = 120 + depth * y_step
            positioned.add(node["id"])
            raw_positions[node_id] = x
            return x

        for root in roots:
            assign(root, 0, set())
        for node in node_by_id.values():
            if node["id"] not in positioned:
                assign(node, 0, set())
        nodes = list(node_by_id.values())
        for node in nodes:
            parent_id = node.get("parentId")
            if parent_id in node_by_id:
                edges.append({"source": parent_id, "target": node["id"], "label": "隶属", "kind": "hierarchy"})

        for row in self.db.fetchall(
            "SELECT source_type, source_id, target_type, target_id, relationship_type, description "
            "FROM relationships WHERE book_id=? ORDER BY created_at",
            (book_id,),
        ):
            if row.get("source_type") == "location" and row.get("target_type") == "location":
                if row.get("source_id") in node_by_id and row.get("target_id") in node_by_id:
                    edges.append({
                        "source": row["source_id"], "target": row["target_id"],
                        "label": row.get("relationship_type") or "连接",
                        "description": row.get("description") or "", "kind": "connection",
                    })
        return {"nodes": nodes, "edges": edges, "layoutWarnings": layout_warnings}

    @staticmethod
    def _render_html(title: str, graph: dict[str, Any]) -> str:
        # Escape HTML-sensitive characters before embedding JSON in a script
        # element.  Escaping only the exact lowercase ``</script>`` sequence
        # is insufficient because HTML tag matching is case-insensitive.
        payload = (
            json.dumps(graph, ensure_ascii=False)
            .replace("&", "\\u0026")
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
        )
        safe_title = html.escape(title)
        return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title} · 世界观地图</title><link rel="icon" href="data:,">
<style>
*{{box-sizing:border-box}} body{{margin:0;background:#08111f;color:#dbeafe;font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif;overflow:hidden}}
.top{{height:64px;padding:14px 20px;display:flex;align-items:center;gap:14px;border-bottom:1px solid #263852;background:#0d1a2b}}
h1{{font-size:18px;margin:0;color:#f8fafc}} .tag{{font-size:12px;color:#86efac;border:1px solid #216e4b;border-radius:999px;padding:4px 9px}}
.tools{{margin-left:auto;display:flex;gap:6px}} button{{border:1px solid #395271;background:#13243a;color:#dbeafe;padding:6px 10px;border-radius:7px;cursor:pointer}}button:hover{{border-color:#60a5fa}}
#map{{width:100vw;height:calc(100vh - 64px);touch-action:none;cursor:grab}} #map:active{{cursor:grabbing}}
.edge{{stroke:#4b6b91;stroke-width:2;opacity:.75}} .edge.connection{{stroke:#c084fc;stroke-dasharray:7 5}} .edge-label{{fill:#94a3b8;font-size:11px;text-anchor:middle}}
.node rect{{fill:#12243a;stroke:#4f78a8;stroke-width:2;rx:12}} .node:hover rect{{fill:#183653;stroke:#60a5fa}} .node text{{pointer-events:none}}
.node .name{{fill:#f8fafc;font-size:14px;font-weight:650;text-anchor:middle}} .node .type{{fill:#93c5fd;font-size:11px;text-anchor:middle}}
.empty{{position:fixed;inset:64px 0 0;display:grid;place-items:center;color:#94a3b8}} .hint{{position:fixed;left:20px;bottom:18px;background:#0d1a2bdd;border:1px solid #263852;border-radius:8px;padding:8px 11px;font-size:12px;color:#93a4bb}}
</style></head><body>
<header class="top"><h1>{safe_title} · 世界观地图</h1><span class="tag">HTML 渲染备用地图</span><div class="tools"><button id="zoom-in">放大</button><button id="zoom-out">缩小</button><button id="reset">重置视图</button></div></header>
<svg id="map" aria-label="小说世界观地图"></svg><div class="hint">滚轮缩放 · 拖拽平移 · 点击地点查看详情</div>
<script>
const data={payload}; const svg=document.getElementById('map'); const ns='http://www.w3.org/2000/svg';
const width=Math.max(1200,Math.max(...(data.nodes||[]).map(n=>n.x||0),0)+260), height=Math.max(720,Math.max(...(data.nodes||[]).map(n=>n.y||0),0)+160);
svg.setAttribute('viewBox',`0 0 ${{width}} ${{height}}`); const scene=document.createElementNS(ns,'g'); svg.appendChild(scene); const edgeLayer=document.createElementNS(ns,'g'); const nodeLayer=document.createElementNS(ns,'g'); scene.append(edgeLayer,nodeLayer);
const byId=Object.fromEntries((data.nodes||[]).map(n=>[n.id,n]));
for(const edge of (data.edges||[])){{const a=byId[edge.source],b=byId[edge.target];if(!a||!b)continue;const line=document.createElementNS(ns,'line');line.setAttribute('x1',a.x);line.setAttribute('y1',a.y);line.setAttribute('x2',b.x);line.setAttribute('y2',b.y);line.setAttribute('class','edge '+(edge.kind==='connection'?'connection':''));edgeLayer.appendChild(line);if(edge.label){{const label=document.createElementNS(ns,'text');label.setAttribute('x',(a.x+b.x)/2);label.setAttribute('y',(a.y+b.y)/2-5);label.setAttribute('class','edge-label');label.textContent=edge.label;edgeLayer.appendChild(label)}}}}
for(const node of (data.nodes||[])){{const g=document.createElementNS(ns,'g');g.setAttribute('class','node');g.setAttribute('transform',`translate(${{node.x}},${{node.y}})`);const rect=document.createElementNS(ns,'rect');rect.setAttribute('x','-100');rect.setAttribute('y','-34');rect.setAttribute('width','200');rect.setAttribute('height','68');g.appendChild(rect);const name=document.createElementNS(ns,'text');name.setAttribute('class','name');name.setAttribute('y','-3');name.textContent=String(node.name||'').slice(0,22);g.appendChild(name);const type=document.createElementNS(ns,'text');type.setAttribute('class','type');type.setAttribute('y','19');type.textContent=node.type||'地点';g.appendChild(type);g.addEventListener('click',()=>alert(`${{node.name}}\\n${{node.description||'暂无描述'}}${{node.significance?'\\n\\n意义：'+node.significance:''}}`));nodeLayer.appendChild(g)}}
let tx=0,ty=0,scale=1,drag=false,sx=0,sy=0;function update(){{scene.setAttribute('transform',`translate(${{tx}} ${{ty}}) scale(${{scale}})`)}}function zoom(f){{scale=Math.max(.35,Math.min(2.8,scale*f));update()}}svg.addEventListener('wheel',e=>{{e.preventDefault();zoom(e.deltaY<0?1.1:.9)}},{{passive:false}});svg.addEventListener('pointerdown',e=>{{if(e.target.closest('.node'))return;drag=true;sx=e.clientX-tx;sy=e.clientY-ty;svg.setPointerCapture(e.pointerId)}});svg.addEventListener('pointermove',e=>{{if(!drag)return;tx=e.clientX-sx;ty=e.clientY-sy;update()}});svg.addEventListener('pointerup',()=>drag=false);document.getElementById('zoom-in').onclick=()=>zoom(1.2);document.getElementById('zoom-out').onclick=()=>zoom(.83);document.getElementById('reset').onclick=()=>{{tx=0;ty=0;scale=1;update()}};update();
</script></body></html>"""
