"""可视化模块 - 思维导图、时间轴、地图生成"""

import json
from pathlib import Path
from ..core.models import StoryProject


class MindMapGenerator:
    """思维导图生成器 - 使用Mermaid.js"""

    def generate_html(self, data: dict, output_path: str) -> str:
        """生成思维导图HTML文件"""
        html = self._render_html(data)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return str(path)

    def generate_from_project(self, project: StoryProject, output_dir: str) -> str:
        """从项目生成思维导图"""
        data = self._build_mindmap_data(project)
        output_path = Path(output_dir) / "mindmap.html"
        return self.generate_html(data, str(output_path))

    def _build_mindmap_data(self, project: StoryProject) -> dict:
        """构建思维导图数据"""
        mindmap = {"root": {"text": project.name, "children": []}}

        # 世界观分支
        world_branch = {"text": "🌍 世界观", "children": []}
        w = project.world
        if w.core_conflict:
            world_branch["children"].append({"text": f"⚔️ 核心矛盾: {w.core_conflict}"})
        if w.power_system:
            world_branch["children"].append({"text": f"⚡ 力量体系: {w.power_system}"})
        if w.world_rules:
            rules = {"text": "📜 世界规则", "children": [{"text": r} for r in w.world_rules]}
            world_branch["children"].append(rules)
        mindmap["root"]["children"].append(world_branch)

        # 角色分支
        if project.characters:
            char_branch = {"text": "👥 人物关系", "children": []}
            for name, char in project.characters.items():
                icon = "⭐" if char.role == "主角" else "👤"
                char_node = {"text": f"{icon} {name} ({char.role})", "children": []}
                for rel_name, rel_type in char.relationships.items():
                    char_node["children"].append({"text": f"→ {rel_name}: {rel_type}"})
                char_branch["children"].append(char_node)
            mindmap["root"]["children"].append(char_branch)

        # 势力分支
        if project.factions:
            fac_branch = {"text": "🏛️ 势力分布", "children": []}
            for name, fac in project.factions.items():
                fac_node = {"text": f"🏴 {name}", "children": []}
                if fac.leader:
                    fac_node["children"].append({"text": f"👑 {fac.leader}"})
                if fac.allies:
                    fac_node["children"].append({"text": f"🤝 盟友: {', '.join(fac.allies)}"})
                if fac.enemies:
                    fac_node["children"].append({"text": f"⚔️ 敌对: {', '.join(fac.enemies)}"})
                fac_branch["children"].append(fac_node)
            mindmap["root"]["children"].append(fac_branch)

        # 地图分支
        if project.locations:
            map_branch = {"text": "🗺️ 地图设定", "children": []}
            for name, loc in project.locations.items():
                loc_node = {"text": f"📍 {name}", "children": []}
                if loc.faction:
                    loc_node["children"].append({"text": f"所属: {loc.faction}"})
                if loc.connected_to:
                    loc_node["children"].append({"text": f"🔗 连接: {', '.join(loc.connected_to)}"})
                map_branch["children"].append(loc_node)
            mindmap["root"]["children"].append(map_branch)

        # 故事结构
        if project.volumes:
            story_branch = {"text": "📖 故事结构", "children": []}
            for vol in project.volumes:
                vol_node = {"text": f"📚 {vol.title}", "children": []}
                for arc in vol.arcs:
                    arc_node = {"text": f"📎 {arc.name}", "children": []}
                    for event in arc.key_events:
                        arc_node["children"].append({"text": f"• {event}"})
                    vol_node["children"].append(arc_node)
                story_branch["children"].append(vol_node)
            mindmap["root"]["children"].append(story_branch)

        # 伏笔分支
        hooks = project.get_open_foreshadowing()
        if hooks:
            hook_branch = {"text": "🪝 伏笔与钩子", "children": []}
            for h in hooks:
                hook_branch["children"].append({
                    "text": f"[{h.id}] {h.description}"
                })
            mindmap["root"]["children"].append(hook_branch)

        return mindmap

    def _render_html(self, data: dict) -> str:
        """渲染为HTML"""
        import html
        json_data = json.dumps(data, ensure_ascii=False).replace('</script>', '<\\/script>')
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>小说思维导图</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; background: #1a1a2e; color: #eee; }}
        #container {{ width: 100vw; height: 100vh; overflow: auto; }}
        canvas {{ display: block; }}
        .node {{ position: absolute; padding: 8px 16px; border-radius: 8px; cursor: pointer;
                 transition: all 0.3s; font-size: 14px; white-space: nowrap; }}
        .node:hover {{ transform: scale(1.1); box-shadow: 0 0 20px rgba(255,255,255,0.3); }}
        .node.root {{ background: #e94560; font-size: 20px; font-weight: bold; padding: 12px 24px; }}
        .node.level1 {{ background: #16213e; border: 2px solid #0f3460; }}
        .node.level2 {{ background: #1a1a2e; border: 1px solid #533483; font-size: 13px; }}
        .node.level3 {{ background: #16213e; border: 1px solid #2c3e50; font-size: 12px; opacity: 0.9; }}
        h1 {{ text-align: center; padding: 20px; color: #e94560; }}
        .legend {{ position: fixed; bottom: 20px; right: 20px; background: rgba(0,0,0,0.7);
                   padding: 15px; border-radius: 10px; font-size: 12px; }}
    </style>
</head>
<body>
    <h1>📖 {html.escape(data['root']['text'])} - 思维导图</h1>
    <div id="container">
        <canvas id="canvas"></canvas>
    </div>
    <div class="legend">
        <div>🌍 世界观 | 👥 角色 | 🏛️ 势力</div>
        <div>🗺️ 地图 | 📖 故事 | 🪝 伏笔</div>
    </div>
    <script>
        const data = {json_data}  ;
        const canvas = document.getElementById('canvas');
        const ctx = canvas.getContext('2d');
        const container = document.getElementById('container');

        canvas.width = Math.max(2000, window.innerWidth);
        canvas.height = Math.max(1500, window.innerHeight);

        const centerX = canvas.width / 2;
        const centerY = canvas.height / 2;
        const nodes = [];

        function layoutNode(node, x, y, angle, radius, level) {{
            node._x = x;
            node._y = y;
            node._level = level;
            nodes.push(node);

            if (!node.children) return;
            const children = node.children;
            const spread = Math.min(Math.PI * 1.5, children.length * 0.4);
            const startAngle = angle - spread / 2;

            children.forEach((child, i) => {{
                const childAngle = startAngle + (children.length > 1 ? (i / (children.length - 1)) * spread : 0);
                const childX = x + Math.cos(childAngle) * radius;
                const childY = y + Math.sin(childAngle) * radius;
                layoutNode(child, childX, childY, childAngle, radius * 0.75, level + 1);
            }});
        }}

        layoutNode(data.root, centerX, centerY, 0, 250, 0);

        // 绘制连线
        ctx.strokeStyle = 'rgba(233, 69, 96, 0.3)';
        ctx.lineWidth = 1.5;
        nodes.forEach(node => {{
            if (node.children) {{
                node.children.forEach(child => {{
                    ctx.beginPath();
                    ctx.moveTo(node._x, node._y);
                    ctx.lineTo(child._x, child._y);
                    ctx.stroke();
                }});
            }}
        }});

        // 绘制节点
        nodes.forEach(node => {{
            const div = document.createElement('div');
            div.className = `node level${{node._level}}`;
            if (node._level === 0) div.className = 'node root';
            div.textContent = node.text;
            div.style.left = node._x + 'px';
            div.style.top = node._y + 'px';
            div.style.transform = 'translate(-50%, -50%)';
            container.appendChild(div);
        }});
    </script>
</body>
</html>"""


class TimelineGenerator:
    """时间轴生成器"""

    def generate_html(self, project: StoryProject, output_path: str) -> str:
        """生成时间轴HTML"""
        events = self._build_timeline(project)
        html = self._render_html(project.name, events)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return str(path)

    def _build_timeline(self, project: StoryProject) -> list:
        """构建时间轴事件"""
        events = []
        for num, ch in sorted(project.chapters.items()):
            events.append({
                "chapter": num,
                "title": ch.title,
                "events": ch.key_events,
                "characters": ch.characters_appeared,
                "location": ch.locations_used[0] if ch.locations_used else "",
            })
        return events

    def _render_html(self, title: str, events: list) -> str:
        import html
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>{html.escape(title)} - 故事时间轴</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Microsoft YaHei', sans-serif; background: #0d1117; color: #c9d1d9; padding: 40px; }}
        h1 {{ text-align: center; color: #58a6ff; margin-bottom: 40px; }}
        .timeline {{ position: relative; max-width: 800px; margin: 0 auto; }}
        .timeline::before {{ content: ''; position: absolute; left: 50%; width: 3px;
                            background: linear-gradient(to bottom, #58a6ff, #bc8cff);
                            top: 0; bottom: 0; transform: translateX(-50%); }}
        .event {{ position: relative; margin: 30px 0; display: flex; align-items: flex-start; }}
        .event:nth-child(odd) {{ flex-direction: row; }}
        .event:nth-child(even) {{ flex-direction: row-reverse; }}
        .event-content {{ width: 45%; padding: 20px; background: #161b22; border-radius: 12px;
                         border: 1px solid #30363d; transition: all 0.3s; }}
        .event-content:hover {{ border-color: #58a6ff; transform: translateY(-3px);
                               box-shadow: 0 5px 20px rgba(88,166,255,0.2); }}
        .event:nth-child(odd) .event-content {{ margin-right: auto; }}
        .event:nth-child(even) .event-content {{ margin-left: auto; }}
        .event-dot {{ position: absolute; left: 50%; transform: translateX(-50%);
                     width: 16px; height: 16px; background: #58a6ff; border-radius: 50%;
                     border: 3px solid #0d1117; z-index: 1; }}
        .chapter-num {{ color: #58a6ff; font-weight: bold; font-size: 18px; }}
        .chapter-title {{ color: #f0f6fc; font-size: 16px; margin: 8px 0; }}
        .detail {{ color: #8b949e; font-size: 13px; margin-top: 8px; }}
        .tag {{ display: inline-block; padding: 2px 8px; background: #1f6feb33;
               border-radius: 10px; font-size: 11px; color: #58a6ff; margin: 2px; }}
    </style>
</head>
<body>
    <h1>📖 {html.escape(title)} - 故事时间轴</h1>
    <div class="timeline">
        {"".join(self._render_event(e, i) for i, e in enumerate(events))}
    </div>
</body>
</html>"""

    def _render_event(self, event: dict, index: int) -> str:
        import html
        chars = "".join(f'<span class="tag">{html.escape(c)}</span>' for c in event.get("characters", []))
        evts = html.escape("、".join(event.get("events", [])))
        loc = f'<span class="tag">📍 {html.escape(event["location"])}</span>' if event.get("location") else ""
        title = html.escape(event.get('title', ''))
        return f"""
        <div class="event">
            <div class="event-dot"></div>
            <div class="event-content">
                <div class="chapter-num">第{event['chapter']}章</div>
                <div class="chapter-title">{title}</div>
                <div class="detail">{evts}</div>
                <div>{chars} {loc}</div>
            </div>
        </div>"""
