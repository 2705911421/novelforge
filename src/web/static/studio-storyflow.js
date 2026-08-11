/* global PAGES, S, api, bookName, esc, escAttr, header, go, toast */
(function () {
  'use strict';

  const VIEW_META = {
    story: { label: 'Story Flow', sub: '剧情推进', strategy: 'layered' },
    character: { label: '人物视图', sub: '关系与状态', strategy: 'radial' },
    timeline: { label: '时间线', sub: '叙事顺序 × 故事时间', strategy: 'chronological' },
    world: { label: '世界图', sub: '层级与势力', strategy: 'hierarchical' },
    foreshadow: { label: '伏笔生命周期', sub: '埋下 → 推进 → 回收', strategy: 'progression' },
    context: { label: 'Context View', sub: '章节上下文候选', strategy: 'focused' },
  };
  const TYPE_LABEL = {
    Book: '作品', Volume: '卷', Arc: '篇章', Chapter: '章节', Scene: '场景', Event: '事件',
    Character: '人物', Faction: '势力', Location: '地点', Item: '物品', PlotThread: '剧情线',
    Foreshadow: '伏笔', Secret: '秘密', StoryGoal: '故事目标', Conflict: '冲突',
    TimelinePoint: '时间点', StoryBibleEntry: '设定', Knowledge: '知识', Relationship: '关系',
    PlanningNode: '规划', Fact: '事实', StoryState: '故事状态',
  };
  const VIEW_TYPES = {
    story: ['Chapter', 'Scene', 'Event', 'PlotThread', 'Foreshadow', 'Conflict', 'Character', 'Location', 'Fact', 'PlanningNode'],
    character: ['Character', 'Relationship', 'Knowledge', 'Faction', 'Event', 'Location', 'Chapter', 'Fact', 'Foreshadow'],
    timeline: ['TimelinePoint', 'Event', 'Chapter', 'Character', 'Location', 'Foreshadow'],
    world: ['Location', 'Faction', 'Character', 'Event', 'Chapter'],
    foreshadow: ['Foreshadow', 'Chapter', 'Event', 'Character', 'PlotThread'],
    context: ['Chapter', 'Character', 'Location', 'Event', 'Foreshadow', 'Fact', 'StoryBibleEntry', 'StoryState'],
  };
  const TYPE_VIEW = {
    Character: 'character', Relationship: 'character', Knowledge: 'character',
    Location: 'world', Faction: 'world',
    Foreshadow: 'foreshadow',
    TimelinePoint: 'timeline',
    Chapter: 'story', Event: 'story', PlotThread: 'story', Conflict: 'story', PlanningNode: 'story',
  };
  const STATUS_LABEL = { CANON: 'CANON', ACCEPTED: 'ACCEPTED', PLANNED: 'PLANNED', CANDIDATE: 'CANDIDATE', DRAFT: 'DRAFT', SUPERSEDED: 'SUPERSEDED', STALE: 'STALE', CONFLICT: 'CONFLICT' };

  let state = null;

  function text(value) {
    return esc(String(value == null ? '' : value));
  }

  function attr(value) {
    return escAttr(String(value == null ? '' : value));
  }

  function nodeLabel(type) {
    return TYPE_LABEL[type] || type || '节点';
  }

  function statusLabel(status) {
    const value = String(status || 'CANON').toUpperCase();
    return STATUS_LABEL[value] || value;
  }

  function statusClass(status) {
    const value = String(status || 'CANON').toLowerCase();
    return `status-${value}`;
  }

  function currentBook() {
    return encodeURIComponent(S.book || '');
  }

  function visibleNodes() {
    return (state && state.graph && state.graph.nodes || []).filter((node) => !node.hidden);
  }

  function selectedNodes() {
    const ids = state ? state.selected : new Set();
    return (state && state.graph && state.graph.nodes || []).filter((node) => ids.has(node.id));
  }

  function nodeById(id) {
    return (state && state.graph && state.graph.nodes || []).find((node) => node.id === id);
  }

  function setSelected(ids, additive) {
    const next = additive ? new Set(state.selected) : new Set();
    ids.forEach((id) => {
      if (additive && next.has(id)) next.delete(id);
      else next.add(id);
    });
    state.selected = next;
    state.detail = null;
    refreshNodeSelection();
    renderSidebar();
    renderInspector();
    const only = selectedNodes()[0];
    if (only) loadNodeDetail(only.id);
  }

  function refreshNodeSelection() {
    document.querySelectorAll('.sf-node').forEach((element) => {
      const id = element.dataset.nodeId;
      element.classList.toggle('is-selected', state.selected.has(id));
      element.classList.toggle('is-focused', id === state.graph?.focus);
    });
    document.querySelectorAll('.sf-minimap-node').forEach((element) => {
      element.classList.toggle('is-selected', state.selected.has(element.dataset.nodeId));
    });
  }

  function renderToolbar() {
    const toolbar = document.getElementById('sf-toolbar');
    if (!toolbar) return;
    toolbar.innerHTML = `
      <div class="sf-toolbar-group">
        <label class="sr-only" for="sf-view-select">StoryFlow 视图</label>
        <select id="sf-view-select" class="sf-view-select" aria-label="StoryFlow 视图">
          ${Object.entries(VIEW_META).map(([key, meta]) => `<option value="${key}" ${state.view === key ? 'selected' : ''}>${text(meta.label)}</option>`).join('')}
        </select>
        <span class="sf-toolbar-caption">${text(VIEW_META[state.view]?.strategy || '')}</span>
      </div>
      <div class="sf-spacer"></div>
      <div class="sf-search-wrap">
        <input id="sf-search" class="sf-search-input" type="search" autocomplete="off" placeholder="搜索人物、章节、伏笔…" aria-label="搜索故事图">
        <div id="sf-search-results" class="sf-search-results" hidden></div>
      </div>
      <button class="btn btn-sm btn-secondary" data-sf-action="auto-layout" title="按当前视图重新布局">自动布局</button>
      <button class="btn btn-sm btn-secondary" data-sf-action="save-layout" title="保存当前工作区位置">保存布局</button>
    `;
    toolbar.insertAdjacentHTML('beforeend', '<button class="btn btn-sm btn-primary" data-sf-action="generate-intent" title="将选中 Story Flow 保存为章节计划">生成章节计划</button><button class="btn btn-sm btn-secondary" data-sf-action="generate-candidates" title="通过持久模型任务生成候选分支">生成候选分支</button><button class="btn btn-sm btn-secondary" data-sf-action="analyze-selection" title="将选中子图交给持久模型分析">AI 分析选择</button><button class="btn btn-sm btn-ghost" data-sf-action="adopt-candidate" title="将选中候选纳入计划">采用候选</button><button class="btn btn-sm btn-ghost" data-sf-action="discard-candidate" title="将选中候选标记为废弃">丢弃候选</button>');
    toolbar.querySelector('#sf-view-select').addEventListener('change', (event) => {
      state.view = event.target.value;
      state.focus = '';
      state.selected = new Set();
      state.detail = null;
      loadGraph();
    });
    const search = toolbar.querySelector('#sf-search');
    search.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') runSearch(search.value);
      if (event.key === 'Escape') hideSearchResults();
    });
    search.addEventListener('input', () => {
      window.clearTimeout(state.searchTimer);
      if (!search.value.trim()) {
        hideSearchResults();
        return;
      }
      state.searchTimer = window.setTimeout(() => runSearch(search.value), 260);
    });
    toolbar.querySelector('[data-sf-action="generate-intent"]').addEventListener('click', generateIntentFromSelection);
    toolbar.querySelector('[data-sf-action="generate-candidates"]').addEventListener('click', generateCandidateBranches);
    toolbar.querySelector('[data-sf-action="analyze-selection"]').addEventListener('click', analyzeSelection);
    toolbar.querySelector('[data-sf-action="adopt-candidate"]').addEventListener('click', () => decideCandidate('adopt'));
    toolbar.querySelector('[data-sf-action="discard-candidate"]').addEventListener('click', () => decideCandidate('discard'));
    toolbar.querySelector('[data-sf-action="auto-layout"]').addEventListener('click', autoLayout);
    toolbar.querySelector('[data-sf-action="save-layout"]').addEventListener('click', saveLayout);
  }

  function renderSidebar() {
    const sidebar = document.getElementById('sf-sidebar');
    if (!sidebar) return;
    const graph = state.graph || { nodes: [], edges: [], meta: {} };
    const typeCounts = graph.nodes.reduce((counts, node) => {
      counts[node.type] = (counts[node.type] || 0) + 1;
      return counts;
    }, {});
    const types = VIEW_TYPES[state.view] || [];
    sidebar.innerHTML = `
      <div class="sf-panel-title"><span>Story Views</span><small>${text(VIEW_META[state.view]?.sub || '')}</small></div>
      <div class="sf-view-list">
        ${Object.entries(VIEW_META).map(([key, meta]) => `
          <button class="sf-view-button ${state.view === key ? 'is-active' : ''}" data-sf-view="${key}">
            <span>${text(meta.label)}</span><small>${text(meta.strategy)}</small>
          </button>`).join('')}
      </div>
      <div class="sf-panel-title"><span>渐进展开</span><small>Focus / Depth</small></div>
      <div class="sf-depth" aria-label="展开深度">
        ${[1, 2, 3].map((depth) => `<button class="${state.depth === depth ? 'is-active' : ''}" data-sf-depth="${depth}">Depth ${depth}</button>`).join('')}
      </div>
      <div class="sf-filter-block">
        <div class="sf-panel-title"><span>过滤器</span><small>${text(graph.meta.returnedNodes || 0)} nodes</small></div>
        <div class="sf-filter-row"><label for="sf-status-filter">状态</label><select id="sf-status-filter" class="sf-filter-select">
          <option value="">全部</option>${['CANON', 'PLANNED', 'CANDIDATE', 'DRAFT', 'STALE', 'CONFLICT'].map((status) => `<option value="${status}" ${state.statuses.includes(status) ? 'selected' : ''}>${status}</option>`).join('')}
        </select></div>
        <div class="sf-filter-row"><label>章节范围</label><div class="sf-filter-range"><input id="sf-chapter-from" class="sf-filter-input" inputmode="numeric" placeholder="起" value="${attr(state.chapterFrom || '')}"><span>–</span><input id="sf-chapter-to" class="sf-filter-input" inputmode="numeric" placeholder="止" value="${attr(state.chapterTo || '')}"></div></div>
        <div class="sf-panel-title" style="margin-top:12px"><span>节点类型</span><small>可多选</small></div>
        <div class="sf-type-list">${types.map((type) => `<button class="sf-type-chip ${state.types.includes(type) ? 'is-active' : ''}" data-sf-type="${type}">${text(nodeLabel(type))} ${typeCounts[type] ? `<span>${typeCounts[type]}</span>` : ''}</button>`).join('')}</div>
      </div>
      <div class="sf-filter-block">
        <div class="sf-panel-title"><span>当前子图</span><small>${graph.meta.focused ? 'focused' : 'bounded'}</small></div>
        <div class="sf-graph-stats">
          <div class="sf-stat"><b>${text(graph.meta.returnedNodes || 0)}</b><span>节点</span></div>
          <div class="sf-stat"><b>${text(graph.meta.returnedEdges || 0)}</b><span>语义边</span></div>
          <div class="sf-stat"><b>${text(state.depth)}</b><span>展开层级</span></div>
          <div class="sf-stat"><b>${graph.meta.truncated ? '是' : '否'}</b><span>有截断</span></div>
        </div>
        <p class="dim-note" style="margin:9px 2px 0;font-size:10px">事实来源：${text(graph.meta.canonicalSource || 'sqlite')} · 布局属于工作区状态</p>
      </div>
      <div class="sf-filter-block">
        <div class="sf-panel-title"><span>节点清单</span><small>点击定位</small></div>
        <div class="sf-node-list">${graph.nodes.slice(0, 80).map((node) => `<button class="sf-neighbor-row" data-sf-select="${attr(node.id)}"><span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><small style="color:var(--text-muted)">${text(nodeLabel(node.type))}</small> ${text(node.title)}</span><span class="sf-neighbor-edge">${text(statusLabel(node.status))}</span></button>`).join('') || '<p class="dim-note">当前焦点没有可显示节点。</p>'}</div>
      </div>
    `;
    sidebar.querySelectorAll('[data-sf-view]').forEach((button) => button.addEventListener('click', () => {
      state.view = button.dataset.sfView;
      state.focus = '';
      state.selected = new Set();
      state.detail = null;
      loadGraph();
    }));
    sidebar.querySelectorAll('[data-sf-depth]').forEach((button) => button.addEventListener('click', () => {
      state.depth = Number(button.dataset.sfDepth);
      loadGraph();
    }));
    sidebar.querySelectorAll('[data-sf-type]').forEach((button) => button.addEventListener('click', () => {
      const type = button.dataset.sfType;
      state.types = state.types.includes(type) ? state.types.filter((item) => item !== type) : [...state.types, type];
      loadGraph();
    }));
    sidebar.querySelector('#sf-status-filter').addEventListener('change', (event) => {
      state.statuses = event.target.value ? [event.target.value] : [];
      loadGraph();
    });
    const applyRange = () => {
      const from = sidebar.querySelector('#sf-chapter-from').value.trim();
      const to = sidebar.querySelector('#sf-chapter-to').value.trim();
      state.chapterFrom = from && /^\d+$/.test(from) ? Number(from) : '';
      state.chapterTo = to && /^\d+$/.test(to) ? Number(to) : '';
      loadGraph();
    };
    sidebar.querySelector('#sf-chapter-from').addEventListener('change', applyRange);
    sidebar.querySelector('#sf-chapter-to').addEventListener('change', applyRange);
    sidebar.querySelectorAll('[data-sf-select]').forEach((button) => button.addEventListener('click', () => {
      const id = button.dataset.sfSelect;
      const node = nodeById(id);
      if (!node) return;
      state.selected = new Set([id]);
      state.focus = id;
      refreshNodeSelection();
      renderInspector();
      loadNodeDetail(id);
      centerOn(node);
    }));
  }

  function renderCanvas() {
    const canvas = document.getElementById('sf-canvas');
    if (!canvas) return;
    const graph = state.graph || { nodes: [], edges: [] };
    if (!graph.nodes.length) {
      canvas.innerHTML = '<div class="sf-canvas-empty"><div><strong>当前视图没有可显示的故事事实</strong><span>尝试降低过滤条件，或先在章节工作台建立真实内容。</span></div></div>';
      return;
    }
    canvas.innerHTML = `
      <svg id="sf-edge-layer" class="sf-edge-layer" aria-label="故事语义连线"><defs><marker id="sf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#f4a261"></path></marker></defs><g id="sf-edge-group"></g></svg>
      <div id="sf-world" class="sf-world"></div>
      <div class="sf-canvas-controls"><button data-sf-canvas="zoom-out" title="缩小">−</button><span id="sf-zoom-label" class="sf-zoom-label">100%</span><button data-sf-canvas="zoom-in" title="放大">+</button><button data-sf-canvas="fit" title="适合画布">适</button><button data-sf-canvas="reset" title="重置视图">复</button></div>
      <div id="sf-minimap" class="sf-minimap" aria-label="Minimap"></div>
      <div class="sf-canvas-hint">拖动画布 · 滚轮缩放 · Shift 框选 · Ctrl/⌘ 多选 · Delete 隐藏</div>
    `;
    const world = canvas.querySelector('#sf-world');
    world.innerHTML = visibleNodes().map(renderNode).join('');
    applyTransform();
    renderEdges();
    renderMinimap();
    bindCanvasControls(canvas);
    refreshNodeSelection();
  }

  function renderNode(node) {
    const inputs = (node.ports?.inputs || []).slice(0, 3);
    const outputs = (node.ports?.outputs || []).slice(0, 3);
    const metadata = node.metadata || {};
    const meta = [
      metadata.number != null ? `Ch.${metadata.number}` : '',
      metadata.storyTime || metadata.event_time || '',
      metadata.lifecycleStatus || '',
    ].filter(Boolean).slice(0, 3);
    return `<article class="sf-node ${node.collapsed ? 'is-collapsed' : ''} ${node.hidden ? 'is-hidden' : ''}" data-node-id="${attr(node.id)}" style="left:${Number(node.x || 0)}px;top:${Number(node.y || 0)}px" tabindex="0" title="${attr(`${nodeLabel(node.type)} · ${node.title}`)}">
      <div class="sf-node-header"><span class="sf-node-kind">${text(nodeLabel(node.type))}</span><span class="sf-status-badge ${statusClass(node.status)}">${text(statusLabel(node.status))}</span></div>
      <strong class="sf-node-title">${text(node.title)}</strong>
      <div class="sf-node-summary">${text(node.summary || '暂无摘要')}</div>
      <div class="sf-node-meta">${meta.map((item) => `<span class="sf-node-badge">${text(item)}</span>`).join('')}</div>
      <div class="sf-node-ports"><div class="sf-port-column"><span class="sf-port-label">INPUT</span>${inputs.map((port) => `<button type="button" class="sf-port sf-port-handle is-input" data-port-direction="input" data-port-name="${attr(port)}" title="释放语义连接：${attr(port)}"><span>${text(port)}</span></button>`).join('') || '<span class="sf-port is-empty">—</span>'}</div><div class="sf-port-column output"><span class="sf-port-label">OUTPUT</span>${outputs.map((port) => `<button type="button" class="sf-port sf-port-handle is-output" data-port-direction="output" data-port-name="${attr(port)}" title="从此端口拖出：${attr(port)}"><span>${text(port)}</span></button>`).join('') || '<span class="sf-port is-empty">—</span>'}</div></div>
    </article>`;
  }

  function renderEdges() {
    const group = document.getElementById('sf-edge-group');
    if (!group || !state.graph) return;
    const nodes = new Map(state.graph.nodes.map((node) => [node.id, node]));
    const edges = state.graph.edges.filter((edge) => nodes.has(edge.source) && nodes.has(edge.target) && !nodes.get(edge.source).hidden && !nodes.get(edge.target).hidden);
    group.innerHTML = edges.map((edge) => {
      const source = nodes.get(edge.source);
      const target = nodes.get(edge.target);
      const x1 = Number(source.x || 0) + 104;
      const y1 = Number(source.y || 0);
      const x2 = Number(target.x || 0) - 104;
      const y2 = Number(target.y || 0);
      const bend = Math.max(42, Math.abs(x2 - x1) * .42);
      const d = `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
      const midX = (x1 + x2) / 2;
      const midY = (y1 + y2) / 2 - 4;
      const selected = state.selected.has(edge.source) || state.selected.has(edge.target);
      const edgeStatus = String(edge.status || 'CANON').toLowerCase();
      return `<g class="sf-edge" data-edge-id="${attr(edge.id)}"><path class="sf-edge-path ${selected ? 'is-selected' : ''} is-${edgeStatus}" d="${attr(d)}"></path><text class="sf-edge-label ${selected ? '' : 'is-muted'}" x="${midX}" y="${midY}">${text(edge.label || edge.type)}</text></g>`;
    }).join('');
    applyTransform();
  }

  function renderMinimap() {
    const minimap = document.getElementById('sf-minimap');
    if (!minimap || !state.graph || !state.graph.nodes.length) return;
    const nodes = visibleNodes();
    const bounds = graphBounds(nodes);
    const width = 138;
    const height = 82;
    const scale = Math.min(width / Math.max(bounds.width, 1), height / Math.max(bounds.height, 1));
    minimap.innerHTML = nodes.map((node) => {
      const x = 4 + (Number(node.x || 0) - bounds.minX) * scale;
      const y = 4 + (Number(node.y || 0) - bounds.minY) * scale;
      return `<span class="sf-minimap-node" data-node-id="${attr(node.id)}" style="left:${x}px;top:${y}px"></span>`;
    }).join('');
    const canvas = document.getElementById('sf-canvas');
    if (canvas) {
      const rect = canvas.getBoundingClientRect();
      const viewWidth = Math.min(width, rect.width / Math.max(state.transform.scale, .01) * scale);
      const viewHeight = Math.min(height, rect.height / Math.max(state.transform.scale, .01) * scale);
      const viewX = Math.max(4, Math.min(4 + width - viewWidth, 4 + (-state.transform.tx / Math.max(state.transform.scale, .01) - bounds.minX) * scale));
      const viewY = Math.max(4, Math.min(4 + height - viewHeight, 4 + (-state.transform.ty / Math.max(state.transform.scale, .01) - bounds.minY) * scale));
      minimap.insertAdjacentHTML('beforeend', `<span class="sf-minimap-viewport" style="left:${viewX}px;top:${viewY}px;width:${viewWidth}px;height:${viewHeight}px"></span>`);
    }
  }

  function graphBounds(nodes) {
    const source = nodes.length ? nodes : [{ x: 0, y: 0 }];
    const xs = source.map((node) => Number(node.x || 0));
    const ys = source.map((node) => Number(node.y || 0));
    const minX = Math.min(...xs) - 120;
    const maxX = Math.max(...xs) + 120;
    const minY = Math.min(...ys) - 90;
    const maxY = Math.max(...ys) + 90;
    return { minX, maxX, minY, maxY, width: Math.max(1, maxX - minX), height: Math.max(1, maxY - minY) };
  }

  function applyTransform() {
    if (!state) return;
    const transform = `translate(${state.transform.tx}px, ${state.transform.ty}px) scale(${state.transform.scale})`;
    const world = document.getElementById('sf-world');
    if (world) world.style.transform = transform;
    const group = document.getElementById('sf-edge-group');
    if (group) group.setAttribute('transform', `translate(${state.transform.tx} ${state.transform.ty}) scale(${state.transform.scale})`);
    const label = document.getElementById('sf-zoom-label');
    if (label) label.textContent = `${Math.round(state.transform.scale * 100)}%`;
    renderMinimap();
  }

  function graphPoint(clientX, clientY) {
    const canvas = document.getElementById('sf-canvas');
    const rect = canvas.getBoundingClientRect();
    return {
      x: (clientX - rect.left - state.transform.tx) / state.transform.scale,
      y: (clientY - rect.top - state.transform.ty) / state.transform.scale,
    };
  }

  function centerOn(node) {
    const canvas = document.getElementById('sf-canvas');
    if (!canvas || !node) return;
    const rect = canvas.getBoundingClientRect();
    state.transform.tx = rect.width / 2 - Number(node.x || 0) * state.transform.scale;
    state.transform.ty = rect.height / 2 - Number(node.y || 0) * state.transform.scale;
    applyTransform();
  }

  function fitGraph() {
    const canvas = document.getElementById('sf-canvas');
    const nodes = visibleNodes();
    if (!canvas || !nodes.length) return;
    const rect = canvas.getBoundingClientRect();
    const bounds = graphBounds(nodes);
    state.transform.scale = Math.max(.28, Math.min(1.08, Math.min(rect.width / bounds.width, rect.height / bounds.height)));
    state.transform.tx = rect.width / 2 - (bounds.minX + bounds.maxX) / 2 * state.transform.scale;
    state.transform.ty = rect.height / 2 - (bounds.minY + bounds.maxY) / 2 * state.transform.scale;
    applyTransform();
  }

  function zoomAt(clientX, clientY, factor) {
    const canvas = document.getElementById('sf-canvas');
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const oldScale = state.transform.scale;
    const nextScale = Math.max(.2, Math.min(2.2, oldScale * factor));
    state.transform.tx = x - (x - state.transform.tx) * nextScale / oldScale;
    state.transform.ty = y - (y - state.transform.ty) * nextScale / oldScale;
    state.transform.scale = nextScale;
    applyTransform();
  }

  function updateNodePositions() {
    document.querySelectorAll('.sf-node').forEach((element) => {
      const node = nodeById(element.dataset.nodeId);
      if (node) {
        element.style.left = `${Number(node.x || 0)}px`;
        element.style.top = `${Number(node.y || 0)}px`;
      }
    });
    renderEdges();
    renderMinimap();
    state.layoutDirty = true;
  }

  function bindCanvasControls(canvas) {
    canvas.querySelectorAll('[data-sf-canvas]').forEach((button) => button.addEventListener('click', () => {
      const action = button.dataset.sfCanvas;
      if (action === 'zoom-in') zoomAt(canvas.getBoundingClientRect().left + canvas.clientWidth / 2, canvas.getBoundingClientRect().top + canvas.clientHeight / 2, 1.16);
      if (action === 'zoom-out') zoomAt(canvas.getBoundingClientRect().left + canvas.clientWidth / 2, canvas.getBoundingClientRect().top + canvas.clientHeight / 2, .86);
      if (action === 'fit') fitGraph();
      if (action === 'reset') { state.transform = { tx: 0, ty: 0, scale: 1 }; applyTransform(); }
    }));
    canvas.addEventListener('wheel', (event) => {
      event.preventDefault();
      zoomAt(event.clientX, event.clientY, event.deltaY < 0 ? 1.1 : .9);
    }, { passive: false });
    canvas.addEventListener('pointerdown', onCanvasPointerDown);
    canvas.addEventListener('pointermove', onCanvasPointerMove);
    canvas.addEventListener('pointerup', onCanvasPointerUp);
    canvas.addEventListener('pointercancel', onCanvasPointerUp);
    canvas.addEventListener('contextmenu', onCanvasContextMenu);
    canvas.addEventListener('keydown', onCanvasKeyDown);
    canvas.addEventListener('click', () => hideContextMenu());
    canvas.querySelectorAll('.sf-port-handle').forEach((port) => {
      port.addEventListener('pointerdown', onPortPointerDown);
    });
    canvas.querySelectorAll('.sf-node').forEach((nodeElement) => {
      nodeElement.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        event.stopPropagation();
        const id = nodeElement.dataset.nodeId;
        const additive = event.ctrlKey || event.metaKey || event.shiftKey;
        setSelected([id], additive);
        const point = graphPoint(event.clientX, event.clientY);
        const selected = new Set(state.selected);
        const origins = {};
        state.graph.nodes.forEach((node) => {
          if (selected.has(node.id)) origins[node.id] = { x: Number(node.x || 0), y: Number(node.y || 0) };
        });
        state.drag = { pointerId: event.pointerId, start: point, origins, moved: false };
        canvas.setPointerCapture(event.pointerId);
        canvas.classList.add('is-dragging');
      });
      nodeElement.addEventListener('dblclick', (event) => {
        event.stopPropagation();
        const node = nodeById(nodeElement.dataset.nodeId);
        if (node) openNodeAction(node);
      });
    });
  }

  function onCanvasPointerDown(event) {
    if (event.button !== 0 || event.target.closest('.sf-node') || event.target.closest('.sf-canvas-controls')) return;
    hideContextMenu();
    const rect = document.getElementById('sf-canvas').getBoundingClientRect();
    if (event.shiftKey) {
      state.box = { startX: event.clientX - rect.left, startY: event.clientY - rect.top, x: event.clientX - rect.left, y: event.clientY - rect.top };
      renderSelectionBox();
    } else {
      state.pan = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, tx: state.transform.tx, ty: state.transform.ty };
      document.getElementById('sf-canvas').classList.add('is-panning');
    }
    document.getElementById('sf-canvas').setPointerCapture(event.pointerId);
  }

  function onCanvasPointerMove(event) {
    const canvas = document.getElementById('sf-canvas');
    if (state.drag) {
      const point = graphPoint(event.clientX, event.clientY);
      const dx = point.x - state.drag.start.x;
      const dy = point.y - state.drag.start.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) state.drag.moved = true;
      Object.entries(state.drag.origins).forEach(([id, origin]) => {
        const node = nodeById(id);
        if (node) { node.x = origin.x + dx; node.y = origin.y + dy; }
      });
      updateNodePositions();
      return;
    }
    if (state.pan) {
      state.transform.tx = state.pan.tx + event.clientX - state.pan.startX;
      state.transform.ty = state.pan.ty + event.clientY - state.pan.startY;
      applyTransform();
      return;
    }
    if (state.box) {
      const rect = canvas.getBoundingClientRect();
      state.box.x = event.clientX - rect.left;
      state.box.y = event.clientY - rect.top;
      renderSelectionBox();
    }
  }

  function onCanvasPointerUp() {
    const canvas = document.getElementById('sf-canvas');
    if (state.drag) {
      state.drag = null;
      canvas.classList.remove('is-dragging');
    }
    if (state.pan) {
      state.pan = null;
      canvas.classList.remove('is-panning');
    }
    if (state.box) {
      const box = state.box;
      const left = Math.min(box.startX, box.x);
      const right = Math.max(box.startX, box.x);
      const top = Math.min(box.startY, box.y);
      const bottom = Math.max(box.startY, box.y);
      const ids = visibleNodes().filter((node) => {
        const screenX = state.transform.tx + Number(node.x || 0) * state.transform.scale;
        const screenY = state.transform.ty + Number(node.y || 0) * state.transform.scale;
        return screenX >= left - 104 * state.transform.scale && screenX <= right + 104 * state.transform.scale && screenY >= top - 70 * state.transform.scale && screenY <= bottom + 70 * state.transform.scale;
      }).map((node) => node.id);
      state.box = null;
      document.querySelector('.sf-selection-box')?.remove();
      if (ids.length) setSelected(ids, false);
    }
    renderMinimap();
  }

  function renderSelectionBox() {
    const canvas = document.getElementById('sf-canvas');
    if (!canvas || !state.box) return;
    let box = canvas.querySelector('.sf-selection-box');
    if (!box) { box = document.createElement('div'); box.className = 'sf-selection-box'; canvas.appendChild(box); }
    const left = Math.min(state.box.startX, state.box.x);
    const top = Math.min(state.box.startY, state.box.y);
    box.style.left = `${left}px`;
    box.style.top = `${top}px`;
    box.style.width = `${Math.abs(state.box.x - state.box.startX)}px`;
    box.style.height = `${Math.abs(state.box.y - state.box.startY)}px`;
  }

  function portElement(nodeId, direction, portName) {
    return [...document.querySelectorAll('.sf-port-handle')].find((element) => {
      const node = element.closest('.sf-node');
      return node?.dataset.nodeId === nodeId
        && element.dataset.portDirection === direction
        && element.dataset.portName === portName;
    });
  }

  function elementGraphCenter(element) {
    const rect = element.getBoundingClientRect();
    return graphPoint((rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2);
  }

  function connectionPath(source, target) {
    const bend = Math.max(42, Math.abs(target.x - source.x) * .42);
    return `M ${source.x} ${source.y} C ${source.x + bend} ${source.y}, ${target.x - bend} ${target.y}, ${target.x} ${target.y}`;
  }

  function renderConnectionPreview(clientX, clientY) {
    const group = document.getElementById('sf-edge-group');
    if (!group || !state.connection) return;
    group.querySelector('#sf-connection-preview')?.remove();
    const sourceElement = portElement(state.connection.sourceNodeId, 'output', state.connection.sourcePort);
    if (!sourceElement) return;
    const source = elementGraphCenter(sourceElement);
    const target = graphPoint(clientX, clientY);
    const preview = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    preview.id = 'sf-connection-preview';
    preview.setAttribute('class', 'sf-edge-path is-planned sf-connection-preview');
    preview.setAttribute('d', connectionPath(source, target));
    group.appendChild(preview);
  }

  function stopPortDrag() {
    window.removeEventListener('pointermove', onPortPointerMove);
    window.removeEventListener('pointerup', onPortPointerUp);
    document.getElementById('sf-edge-group')?.querySelector('#sf-connection-preview')?.remove();
    document.getElementById('sf-canvas')?.classList.remove('is-connecting');
    state.connection = null;
  }

  function onPortPointerDown(event) {
    if (event.button !== 0 || event.currentTarget.dataset.portDirection !== 'output') return;
    const node = event.currentTarget.closest('.sf-node');
    if (!node) return;
    event.preventDefault();
    event.stopPropagation();
    state.connection = {
      sourceNodeId: node.dataset.nodeId,
      sourcePort: event.currentTarget.dataset.portName,
    };
    document.getElementById('sf-canvas')?.classList.add('is-connecting');
    window.addEventListener('pointermove', onPortPointerMove);
    window.addEventListener('pointerup', onPortPointerUp);
    renderConnectionPreview(event.clientX, event.clientY);
  }

  function onPortPointerMove(event) {
    if (state.connection) renderConnectionPreview(event.clientX, event.clientY);
  }

  async function onPortPointerUp(event) {
    if (!state.connection) return;
    const connection = { ...state.connection };
    const targetElement = document.elementFromPoint(event.clientX, event.clientY)?.closest('.sf-port-handle.is-input');
    stopPortDrag();
    if (!targetElement) {
      toast('连接已取消：请释放到另一个节点的 INPUT 端口。', 'warning');
      return;
    }
    const targetNodeElement = targetElement.closest('.sf-node');
    const sourceNode = nodeById(connection.sourceNodeId);
    const targetNode = targetNodeElement ? nodeById(targetNodeElement.dataset.nodeId) : null;
    if (!sourceNode || !targetNode) return;
    try {
      const query = new URLSearchParams({
        sourceType: sourceNode.type,
        targetType: targetNode.type,
        sourcePort: connection.sourcePort,
        targetPort: targetElement.dataset.portName,
      });
      const result = await api('GET', `/books/${currentBook()}/story-graph/edge-options?${query.toString()}`);
      showEdgeChooser(event.clientX, event.clientY, connection, targetNode, targetElement.dataset.portName, result.options || []);
    } catch (error) {
      toast(`无法读取合法语义连接：${error.message}`, 'error');
    }
  }

  function showEdgeChooser(x, y, connection, targetNode, targetPort, options) {
    hideEdgeChooser();
    if (!options.length) {
      toast(`没有允许的连接：${connection.sourceNodeId} → ${targetNode.id}`, 'warning');
      return;
    }
    const menu = document.createElement('div');
    menu.className = 'sf-context-menu sf-edge-chooser';
    menu.id = 'sf-edge-chooser';
    menu.style.left = `${Math.min(x, window.innerWidth - 230)}px`;
    menu.style.top = `${Math.min(y, window.innerHeight - 220)}px`;
    menu.innerHTML = `<div class="sf-edge-chooser-title">选择语义连接</div>${options.map((option) => `<button data-edge-type="${attr(option.type)}"><b>${text(option.type)}</b><small>${text(option.sourceType)}.${text(option.sourcePort || 'output')} → ${text(option.targetType)}.${text(option.targetPort || 'input')}</small></button>`).join('')}<button data-edge-cancel="1">取消</button>`;
    document.body.appendChild(menu);
    menu.querySelectorAll('[data-edge-type]').forEach((button) => button.addEventListener('click', async () => {
      const edgeType = button.dataset.edgeType;
      hideEdgeChooser();
      try {
        const planning = await api('GET', `/books/${currentBook()}/story-graph/planning`);
        await api('POST', `/books/${currentBook()}/story-graph/planning/edge`, {
          sourceNodeId: connection.sourceNodeId,
          targetNodeId: targetNode.id,
          edgeType,
          sourcePort: connection.sourcePort,
          targetPort,
          status: 'PLANNED',
          expectedRevision: Number(planning.revision || 1),
        });
        state.selected = new Set([connection.sourceNodeId, targetNode.id]);
        await loadGraph();
        toast(`已保存 PLANNED 语义连接：${edgeType}`, 'success');
      } catch (error) {
        toast(`语义连接保存失败：${error.message}`, 'error');
      }
    }));
    menu.querySelector('[data-edge-cancel]')?.addEventListener('click', hideEdgeChooser);
  }

  function hideEdgeChooser() { document.getElementById('sf-edge-chooser')?.remove(); }

  function onCanvasContextMenu(event) {
    event.preventDefault();
    const nodeElement = event.target.closest('.sf-node');
    if (nodeElement) {
      const node = nodeById(nodeElement.dataset.nodeId);
      if (node) showContextMenu(event.clientX, event.clientY, node);
    }
  }

  function showContextMenu(x, y, node) {
    hideContextMenu();
    const menu = document.createElement('div');
    menu.className = 'sf-context-menu';
    menu.id = 'sf-context-menu';
    menu.style.left = `${Math.min(x, window.innerWidth - 180)}px`;
    menu.style.top = `${Math.min(y, window.innerHeight - 150)}px`;
    menu.innerHTML = `<button data-menu-action="focus">聚焦此节点</button><button data-menu-action="expand">展开下一层</button><button data-menu-action="collapse">${node.collapsed ? '展开节点' : '折叠节点'}</button><button data-menu-action="hide">隐藏节点（保存后生效）</button><button data-menu-action="inspect">打开 Inspector</button>`;
    document.body.appendChild(menu);
    menu.querySelectorAll('[data-menu-action]').forEach((button) => button.addEventListener('click', () => {
      const action = button.dataset.menuAction;
      if (action === 'focus') { state.focus = node.id; state.depth = 1; state.selected = new Set([node.id]); loadGraph(); }
      if (action === 'expand') { state.focus = node.id; state.depth = Math.min(3, state.depth + 1); state.selected = new Set([node.id]); loadGraph(); }
      if (action === 'collapse') { node.collapsed = !node.collapsed; renderCanvas(); }
      if (action === 'hide') { node.hidden = true; state.layoutDirty = true; renderCanvas(); }
      if (action === 'inspect') { state.selected = new Set([node.id]); refreshNodeSelection(); renderInspector(); loadNodeDetail(node.id); }
      hideContextMenu();
    }));
  }

  function hideContextMenu() { document.getElementById('sf-context-menu')?.remove(); hideEdgeChooser(); }

  function onCanvasKeyDown(event) {
    if (event.key === 'Delete' || event.key === 'Backspace') {
      selectedNodes().forEach((node) => { node.hidden = true; });
      state.layoutDirty = true;
      renderCanvas();
      event.preventDefault();
    }
    if (event.key === '0') { fitGraph(); event.preventDefault(); }
    if (event.key === '+' || event.key === '=') { zoomAt(window.innerWidth / 2, window.innerHeight / 2, 1.15); event.preventDefault(); }
    if (event.key === '-') { zoomAt(window.innerWidth / 2, window.innerHeight / 2, .87); event.preventDefault(); }
  }

  function renderInspector() {
    const inspector = document.getElementById('sf-inspector');
    if (!inspector) return;
    const nodes = selectedNodes();
    if (!nodes.length) {
      inspector.innerHTML = '<div class="sf-inspector-empty"><div><strong>选择一个故事节点</strong><br>这里会显示它的状态、来源、语义关系和可执行动作。<br><br>Canvas 上的坐标属于工作区，不会写入 StoryFact。</div></div>';
      return;
    }
    if (nodes.length > 1) {
      inspector.innerHTML = `<div class="sf-inspector-head"><div><h3>${nodes.length} 个节点</h3><p>多选子图</p></div><span class="sf-status-badge status-planned">SELECTION</span></div><div class="sf-context-banner">当前选择可以用于后续 AI 分析、生成章节计划或创建候选分支。此版本先提供真实子图与状态，不伪造模型结果。</div><div class="sf-inspector-section"><h4>选中节点</h4><ul class="sf-inspector-list">${nodes.slice(0, 12).map((node) => `<li>${text(nodeLabel(node.type))} · ${text(node.title)}</li>`).join('')}</ul></div>`;
      inspector.insertAdjacentHTML('beforeend', '<div class="sf-inspector-actions"><button class="btn btn-sm btn-primary" data-sf-selection-action="intent">生成章节计划</button><button class="btn btn-sm btn-secondary" data-sf-selection-action="analyze">AI 分析选择</button></div>');
      inspector.querySelector('[data-sf-selection-action="intent"]')?.addEventListener('click', generateIntentFromSelection);
      inspector.querySelector('[data-sf-selection-action="analyze"]')?.addEventListener('click', analyzeSelection);
      if (state.analysisResult) renderAnalysisResult(inspector);
      return;
    }
    const node = nodes[0];
    const detail = state.detail && state.detail.node?.id === node.id ? state.detail : null;
    inspector.innerHTML = renderInspectorNode(node, detail);
    inspector.querySelectorAll('[data-sf-neighbor]').forEach((button) => button.addEventListener('click', () => {
      const target = nodeById(button.dataset.sfNeighbor);
      if (target) { state.selected = new Set([target.id]); refreshNodeSelection(); renderInspector(); loadNodeDetail(target.id); centerOn(target); }
    }));
    inspector.querySelectorAll('[data-sf-inspector-action]').forEach((button) => button.addEventListener('click', () => {
      const action = button.dataset.sfInspectorAction;
      if (action === 'focus') { state.focus = node.id; state.depth = 1; loadGraph(); }
      if (action === 'expand') { state.focus = node.id; state.depth = Math.min(3, state.depth + 1); loadGraph(); }
      if (action === 'context' && node.type === 'Chapter') loadContext(node.id);
      if (action === 'open') openNodeAction(node);
    }));
  }

  function renderInspectorNode(node, detail) {
    const metadata = node.metadata || {};
    const stateData = metadata.state || metadata.characterState || {};
    const neighbors = detail?.neighbors || [];
    const provenance = node.provenance || [];
    const chapter = metadata.number || metadata.chapterNumber || metadata.narrativeOrder || node.chapter_id || '—';
    const status = statusLabel(node.status);
    const knowledge = Array.isArray(stateData.knowledge) ? stateData.knowledge : Array.isArray(metadata.knowledge) ? metadata.knowledge : [];
    const relationSummary = neighbors.slice(0, 12).map((item) => `<div class="sf-neighbor-row"><button data-sf-neighbor="${attr(item.node.id)}">${text(nodeLabel(item.node.type))} · ${text(item.node.title)}</button><span class="sf-neighbor-edge">${text(item.edge.label || item.edge.type)}</span></div>`).join('');
    return `<div class="sf-inspector-head"><div><h3>${text(node.title)}</h3><p>${text(nodeLabel(node.type))} · ${text(status)}</p></div><span class="sf-status-badge ${statusClass(node.status)}">${text(status)}</span></div>
      <div class="sf-inspector-actions"><button class="btn btn-sm btn-secondary" data-sf-inspector-action="focus">聚焦</button><button class="btn btn-sm btn-secondary" data-sf-inspector-action="expand">展开二阶</button>${node.type === 'Chapter' ? '<button class="btn btn-sm btn-secondary" data-sf-inspector-action="context">查看 Context</button>' : ''}<button class="btn btn-sm btn-secondary" data-sf-inspector-action="open">打开来源</button></div>
      <div class="sf-inspector-section"><h4>创作状态</h4><dl class="sf-kv"><dt>摘要</dt><dd>${text(node.summary || '暂无摘要')}</dd><dt>章节</dt><dd>${text(chapter)}</dd><dt>来源表</dt><dd>${text(node.source_type || '—')}</dd><dt>来源 ID</dt><dd>${text(node.source_id || '—')}</dd>${metadata.storyTime || metadata.event_time ? `<dt>故事时间</dt><dd>${text(metadata.storyTime || metadata.event_time)}</dd>` : ''}${metadata.current_location ? `<dt>当前位置</dt><dd>${text(metadata.current_location)}</dd>` : ''}</dl></div>
      ${knowledge.length ? `<div class="sf-inspector-section"><h4>当前知识</h4><ul class="sf-inspector-list">${knowledge.slice(0, 12).map((item) => `<li>${text(typeof item === 'object' ? item.name || item.title || JSON.stringify(item) : item)}</li>`).join('')}</ul></div>` : ''}
      <div class="sf-inspector-section"><h4>语义关系 ${detail ? neighbors.length : ''}</h4>${detail ? (relationSummary || '<p class="dim-note">当前节点没有已投影的一阶语义边。</p>') : '<p class="dim-note">正在从 SQLite 读取邻接关系…</p>'}</div>
      <div class="sf-inspector-section"><h4>Provenance</h4>${provenance.length ? `<div class="sf-provenance">${provenance.slice(0, 6).map((item) => `<div>· ${text(item.kind || 'source')} ${item.table ? `<code>${text(item.table)}</code>` : ''} ${item.id ? `<code>${text(item.id)}</code>` : ''}</div>`).join('')}</div>` : '<p class="sf-provenance">未记录可展示的来源链。</p>'}</div>`;
  }

  async function loadNodeDetail(nodeId) {
    if (!nodeId || !state || !state.graph) return;
    try {
      const result = await api('GET', `/books/${currentBook()}/story-graph/nodes/${encodeURIComponent(nodeId)}`);
      if (state.selected.has(nodeId)) {
        state.detail = result;
        renderInspector();
      }
    } catch (error) {
      if (state.selected.has(nodeId)) {
        state.detailError = error.message;
        renderInspector();
        toast(`节点详情读取失败：${error.message}`, 'error');
      }
    }
  }

  async function loadContext(chapterId) {
    state.contextLoading = true;
    renderInspector();
    try {
      state.context = await api('GET', `/books/${currentBook()}/story-graph/context/${encodeURIComponent(chapterId)}`);
      renderContextInspector();
    } catch (error) {
      state.contextError = error.message;
      renderInspector();
      toast(`Context 读取失败：${error.message}`, 'error');
    } finally {
      state.contextLoading = false;
    }
  }

  function renderContextInspector() {
    const inspector = document.getElementById('sf-inspector');
    if (!inspector || !state.context) return;
    const context = state.context;
    const sources = context.sources || [];
    const breakdown = context.tokenSummary?.breakdown || [];
    inspector.innerHTML = `<div class="sf-inspector-head"><div><h3>Chapter Context</h3><p>${text(context.chapterId)}</p></div><span class="sf-status-badge status-candidate">TRACE</span></div><div class="sf-context-banner">${context.trace?.available ? '这是 GenerationRun 持久化的实际上下文清单。' : text(context.trace?.reason || '当前没有持久化的 Writer token manifest；下面只显示可追溯的候选上下文。')}</div><div class="sf-inspector-section"><h4>${context.trace?.available ? '实际来源' : '候选来源'} ${sources.length}</h4><div>${sources.slice(0, 30).map((source) => `<div class="sf-neighbor-row"><span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><b>${text(nodeLabel(source.type))}</b> · ${text(source.title)}<br><small style="color:var(--text-muted)">${text(source.reason)}</small></span><span class="sf-neighbor-edge">${source.provenance?.length ? '可追溯' : '无来源'}</span></div>`).join('') || '<p class="dim-note">没有候选上下文。</p>'}</div></div><div class="sf-inspector-section"><h4>上下文构成</h4>${breakdown.length ? `<div class="sf-context-breakdown">${breakdown.map((item) => `<div class="sf-context-breakdown-row"><span>${text(item.sourceType)}</span><b>${text(item.contentChars)} chars</b><small>≈ ${text(item.estimatedTokens)} tokens · ${text(item.includedItems)}/${text(item.items)} included</small></div>`).join('')}</div><p class="dim-note">分项 token 是 contentChars/4 的估算；Provider 只把实际 promptTokens/totalTokens 记录为整次 GenerationRun，不把估算冒充分项实测。</p>` : '<p class="dim-note">未记录分项 context manifest。不会把估算值冒充 Writer 实际输入。</p>'}</div><div class="sf-inspector-section"><h4>GenerationRun</h4><dl class="sf-kv"><dt>Run</dt><dd>${text(context.trace?.generationRunId || '—')}</dd><dt>Prompt</dt><dd>${text(context.tokenSummary?.promptTokens ?? '—')} tokens</dd><dt>Total</dt><dd>${text(context.tokenSummary?.totalTokens ?? '—')} tokens</dd><dt>Hash</dt><dd>${text(context.tokenSummary?.promptSha256 || '—')}</dd></dl></div><div class="sf-inspector-actions"><button class="btn btn-sm btn-secondary" data-sf-back-inspector="1">返回节点</button></div>`;
    inspector.querySelector('[data-sf-back-inspector]')?.addEventListener('click', () => renderInspector());
  }

  function openNodeAction(node) {
    if (node.type === 'Chapter') {
      const number = node.metadata?.number;
      if (number != null && typeof window.editChapter === 'function') window.editChapter(number);
      else go('chapters');
      return;
    }
    if (node.type === 'Character') { go('characters'); return; }
    if (node.type === 'Foreshadow') { go('foreshadowing'); return; }
    if (node.type === 'Location' || node.type === 'Faction') { go('world-map'); return; }
    toast(`已定位 ${nodeLabel(node.type)}：${node.title}`, 'success');
  }

  function hideSearchResults() {
    const results = document.getElementById('sf-search-results');
    if (results) { results.hidden = true; results.innerHTML = ''; }
  }

  async function runSearch(query) {
    const term = String(query || '').trim();
    if (!term) return;
    const results = document.getElementById('sf-search-results');
    if (!results) return;
    results.hidden = false;
    results.innerHTML = '<p class="dim-note" style="padding:8px">搜索中…</p>';
    try {
      const response = await api('GET', `/books/${currentBook()}/story-graph/search?q=${encodeURIComponent(term)}&view=all&limit=30`);
      const matches = response.matches || [];
      results.innerHTML = matches.length ? matches.map((match) => `<button class="sf-search-result" data-sf-search-id="${attr(match.id)}" data-sf-search-type="${attr(match.type)}"><small>${text(nodeLabel(match.type))}</small><strong>${text(match.title)}</strong></button>`).join('') : '<p class="dim-note" style="padding:8px">没有匹配的真实节点。</p>';
      results.querySelectorAll('[data-sf-search-id]').forEach((button) => button.addEventListener('click', () => {
        const id = button.dataset.sfSearchId;
        state.focus = id;
        state.depth = 1;
        state.view = TYPE_VIEW[button.dataset.sfSearchType] || 'story';
        state.selected = new Set([id]);
        state.detail = null;
        hideSearchResults();
        loadGraph();
      }));
    } catch (error) {
      results.innerHTML = `<p class="dim-note" style="padding:8px">搜索失败：${text(error.message)}</p>`;
      toast(`Story Graph 搜索失败：${error.message}`, 'error');
    }
  }

  function queryString() {
    const params = new URLSearchParams({ view: state.view, depth: String(state.depth), limit: '240' });
    if (state.focus) params.set('focus', state.focus);
    if (state.types.length) params.set('types', state.types.join(','));
    if (state.statuses.length) params.set('statuses', state.statuses.join(','));
    if (state.chapterFrom) params.set('chapter_from', String(state.chapterFrom));
    if (state.chapterTo) params.set('chapter_to', String(state.chapterTo));
    return params.toString();
  }

  function syncModelWorkOverlap() {
    const page = document.querySelector('.storyflow-page');
    const indicator = document.getElementById('model-work-indicator');
    if (!page || !indicator) return;
    page.classList.toggle('has-model-work', indicator.getAttribute('aria-hidden') !== 'true');
  }

  function observeModelWork() {
    if (!state) return;
    const indicator = document.getElementById('model-work-indicator');
    if (!indicator || typeof MutationObserver === 'undefined') {
      syncModelWorkOverlap();
      return;
    }
    syncModelWorkOverlap();
    state.modelWorkObserver = new MutationObserver(syncModelWorkOverlap);
    state.modelWorkObserver.observe(indicator, { attributes: true, attributeFilter: ['aria-hidden'], childList: true });
  }

  async function loadGraph() {
    if (!state || !S.book) return;
    state.loading = true;
    const canvas = document.getElementById('sf-canvas');
    if (canvas) canvas.innerHTML = '<div class="sf-loading">从 SQLite Story Graph 读取焦点子图…</div>';
    try {
      const graph = await api('GET', `/books/${currentBook()}/story-graph?${queryString()}`);
      state.graph = graph;
      state.focus = graph.focus || state.focus;
      state.selected = new Set([...state.selected].filter((id) => graph.nodes.some((node) => node.id === id)));
      state.detail = null;
      renderToolbar();
      renderSidebar();
      renderCanvas();
      renderInspector();
      window.requestAnimationFrame(fitGraph);
      const selected = selectedNodes()[0];
      if (selected) loadNodeDetail(selected.id);
    } catch (error) {
      state.graph = { nodes: [], edges: [], meta: {} };
      const shell = document.getElementById('sf-canvas');
      if (shell) shell.innerHTML = `<div class="sf-canvas-empty"><div><strong>Story Graph 读取失败</strong><span>${text(error.message)}</span></div></div>`;
      renderSidebar();
      renderInspector();
      toast(`StoryFlow 加载失败：${error.message}`, 'error');
    } finally {
      state.loading = false;
    }
  }

  async function planningRevision() {
    const payload = await api('GET', `/books/${currentBook()}/story-graph/planning`);
    return Number(payload.revision || 1);
  }

  async function generateIntentFromSelection() {
    const ids = selectedNodes().map((node) => node.id);
    if (!ids.length) {
      toast('先在画布上选择至少一个真实 StoryFlow 节点。', 'warning');
      return;
    }
    try {
      const revision = await planningRevision();
      const result = await api('POST', `/books/${currentBook()}/story-graph/planning/intent`, {
        nodeIds: ids,
        save: true,
        expectedRevision: revision,
      });
      const planId = result.planningNode?.id;
      state.selected = planId ? new Set([planId]) : new Set(ids);
      state.focus = planId || state.focus;
      state.detail = null;
      await loadGraph();
      toast('已将选中的真实 Flow 保存为 PLANNED 章节计划；尚未写入 Canon。', 'success');
    } catch (error) {
      toast(`章节计划保存失败：${error.message}`, 'error');
    }
  }

  async function decideCandidate(decision) {
    const ids = selectedNodes().filter((node) => node.type === 'PlanningNode' && node.status === 'CANDIDATE').map((node) => node.id);
    if (!ids.length) {
      toast('当前选择中没有 CANDIDATE 规划节点。', 'warning');
      return;
    }
    try {
      const revision = await planningRevision();
      await api('POST', `/books/${currentBook()}/story-graph/planning/decision`, { nodeIds: ids, decision, expectedRevision: revision });
      await loadGraph();
      toast(decision === 'adopt' ? '候选分支已转为 PLANNED。' : '候选分支已标记为 SUPERSEDED。', 'success');
    } catch (error) {
      toast(`候选决策失败：${error.message}`, 'error');
    }
  }

  async function generateCandidateBranches() {
    const nodes = selectedNodes();
    if (!nodes.length) {
      toast('先选择一个或多个 StoryFlow 节点，再生成候选分支。', 'warning');
      return;
    }
    const first = nodes[0];
    const metadata = first.metadata || {};
    const chapterNumber = Number(metadata.number || metadata.chapterNumber || metadata.narrativeOrder || 0) || 0;
    try {
      const planning = await api('GET', `/books/${currentBook()}/story-graph/planning`);
      const queued = await api('POST', `/books/${currentBook()}/forecast`, {
        branchCount: 3,
        currentChapter: chapterNumber,
        depth: Math.max(3, state.depth),
        nodeId: first.id,
        nodeIds: nodes.map((node) => node.id),
        canvasRevision: Number(planning.revision || 1),
      });
      state.candidateTaskId = queued.taskId;
      toast(`候选分支任务已排队：${queued.taskId}。结果会以 CANDIDATE 覆盖层写入 StoryFlow。`, 'success');
      watchCandidateTask(queued.taskId, first.id, 0);
    } catch (error) {
      toast(`候选分支任务创建失败：${error.message}`, 'error');
    }
  }

  async function watchCandidateTask(taskId, sourceNodeId, attempt) {
    if (!state || state.candidateTaskId !== taskId) return;
    try {
      const task = await api('GET', `/tasks/${encodeURIComponent(taskId)}`);
      if (task.status === 'completed') {
        const branches = task.result?.branches;
        if (!Array.isArray(branches) || !branches.length) {
          toast('候选分支任务完成，但没有返回可持久化的分支。', 'error');
          return;
        }
        await persistCandidateBranches(branches, sourceNodeId);
        return;
      }
      if (['failed', 'cancelled', 'needs_author_decision'].includes(task.status)) {
        toast(`候选分支任务${task.status}：${task.error || task.error_code || '未生成结果'}`, 'error');
        return;
      }
      if (attempt >= 240) {
        toast(`候选分支任务仍在${task.status}，请在任务中心查看，不会把未完成结果显示为候选。`, 'warning');
        return;
      }
      state.candidateTimer = window.setTimeout(() => watchCandidateTask(taskId, sourceNodeId, attempt + 1), 1000);
    } catch (error) {
      if (attempt >= 10) {
        toast(`候选分支任务状态读取失败：${error.message}`, 'error');
        return;
      }
      state.candidateTimer = window.setTimeout(() => watchCandidateTask(taskId, sourceNodeId, attempt + 1), 1500);
    }
  }

  async function persistCandidateBranches(branches, sourceNodeId) {
    let planning = await api('GET', `/books/${currentBook()}/story-graph/planning`);
    let revision = Number(planning.revision || 1);
    let persisted = 0;
    for (const branch of branches.slice(0, 8)) {
      if (!branch || typeof branch !== 'object') continue;
      const result = await api('POST', `/books/${currentBook()}/plot-canvas/apply-branch`, {
        branch,
        sourceNodeId,
        expectedRevision: revision,
      });
      revision = Number(result.revision || revision);
      persisted += 1;
    }
    state.candidateTaskId = null;
    state.focus = sourceNodeId;
    state.depth = Math.max(2, state.depth);
    await loadGraph();
    toast(`已将 ${persisted} 个模型返回分支写入 revisioned planning overlay；当前仍是 CANDIDATE，不会污染 Canon。`, 'success');
  }

  async function analyzeSelection() {
    const nodes = selectedNodes();
    if (!nodes.length) {
      toast('先选择至少一个 StoryFlow 节点，再运行 AI 分析。', 'warning');
      return;
    }
    try {
      const queued = await api('POST', `/books/${currentBook()}/story-graph/actions/analyze`, {
        nodeIds: nodes.map((node) => node.id),
        analysisTypes: ['pace', 'relationship_changes', 'logic_conflicts', 'stale_plot_threads', 'foreshadowing_progress', 'timeline_anomalies', 'repetition', 'next_steps'],
      });
      state.analysisTaskId = queued.taskId;
      state.analysisResult = null;
      toast(`StoryFlow 分析已排队：${queued.taskId}。模型结果会持久化在任务记录中。`, 'success');
      watchAnalysisTask(queued.taskId, 0);
    } catch (error) {
      toast(`StoryFlow 分析任务创建失败：${error.message}`, 'error');
    }
  }

  async function watchAnalysisTask(taskId, attempt) {
    if (!state || state.analysisTaskId !== taskId) return;
    try {
      const result = await api('GET', `/books/${currentBook()}/story-graph/actions/analyze/${encodeURIComponent(taskId)}`);
      if (result.status === 'completed') {
        state.analysisResult = result.result;
        renderInspector();
        toast('StoryFlow 分析完成，报告已从持久化任务结果载入 Inspector。', 'success');
        return;
      }
      if (['failed', 'cancelled', 'needs_author_decision'].includes(result.status)) {
        toast(`StoryFlow 分析${result.status}：${result.error || result.errorCode || '没有报告'}`, 'error');
        return;
      }
      if (attempt >= 240) {
        toast(`StoryFlow 分析仍在${result.status}，请在任务中心查看。`, 'warning');
        return;
      }
      state.analysisTimer = window.setTimeout(() => watchAnalysisTask(taskId, attempt + 1), 1000);
    } catch (error) {
      if (attempt >= 10) {
        toast(`StoryFlow 分析状态读取失败：${error.message}`, 'error');
        return;
      }
      state.analysisTimer = window.setTimeout(() => watchAnalysisTask(taskId, attempt + 1), 1500);
    }
  }

  function renderAnalysisResult(inspector) {
    const result = state.analysisResult;
    if (!result || !inspector) return;
    const findings = Array.isArray(result.findings) ? result.findings : [];
    inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section sf-analysis-result"><h4>AI 分析结果 · ${text(result.source || 'model')}</h4><div class="sf-context-banner">${text(result.summary || '模型没有返回摘要。')}</div><ul class="sf-inspector-list">${findings.map((finding) => `<li><span><b class="sf-severity-${text(finding.severity || 'info')}">${text(finding.kind || 'observation')}</b> ${text(finding.message)}${finding.evidenceNodeIds?.length ? `<small class="sf-analysis-evidence">证据：${text(finding.evidenceNodeIds.join(', '))}</small>` : ''}</span></li>`).join('') || '<li>模型没有返回结构化 findings。</li>'}</ul>${Array.isArray(result.nextSteps) && result.nextSteps.length ? `<h4 style="margin-top:10px">下一步</h4><ul class="sf-inspector-list">${result.nextSteps.map((item) => `<li>${text(item)}</li>`).join('')}</ul>` : ''}</div>`);
  }

  async function saveLayout() {
    if (!state?.graph) return;
    const items = state.graph.nodes.map((node) => ({ nodeId: node.id, x: node.x, y: node.y, collapsed: !!node.collapsed, pinned: !!node.pinned, hidden: !!node.hidden }));
    try {
      await api('POST', `/books/${currentBook()}/story-graph/layout`, { view: state.view, items });
      state.layoutDirty = false;
      toast('StoryFlow 工作区布局已保存。', 'success');
    } catch (error) {
      toast(`布局保存失败：${error.message}`, 'error');
    }
  }

  async function autoLayout() {
    if (!state?.graph) return;
    try {
      const result = await api('POST', `/books/${currentBook()}/story-graph/layout/auto`, { view: state.view, focus: state.focus || null, depth: state.depth, items: [] });
      const positions = new Map((result.items || []).map((item) => [item.nodeId, item]));
      state.graph.nodes.forEach((node) => {
        const position = positions.get(node.id);
        if (position) { node.x = position.x; node.y = position.y; node.collapsed = false; node.pinned = false; node.hidden = false; }
      });
      state.layoutDirty = true;
      renderCanvas();
      fitGraph();
      toast('已按当前视图策略重新布局；点击“保存布局”写入工作区。', 'success');
    } catch (error) {
      toast(`自动布局失败：${error.message}`, 'error');
    }
  }

  function destroy() {
    window.clearTimeout(state?.searchTimer);
    window.clearTimeout(state?.candidateTimer);
    window.clearTimeout(state?.analysisTimer);
    if (state?.connection) stopPortDrag();
    state?.modelWorkObserver?.disconnect();
    hideContextMenu();
  }

  PAGES.storyflow = async function storyflowPage(page) {
    if (state) destroy();
    state = {
      view: 'story', depth: 1, focus: '', types: [], statuses: [], chapterFrom: '', chapterTo: '',
      graph: null, selected: new Set(), detail: null, context: null, transform: { tx: 0, ty: 0, scale: 1 },
      drag: null, pan: null, box: null, connection: null, layoutDirty: false, searchTimer: null, candidateTimer: null, candidateTaskId: null, analysisTimer: null, analysisTaskId: null, analysisResult: null, modelWorkObserver: null,
    };
    if (!S.book) {
      page.innerHTML = header('StoryFlow 故事画布', '请先选择一部作品') + '<div class="storyflow-page"><div class="sf-canvas-empty"><div><strong>StoryFlow 需要真实作品作为焦点</strong><br><button class="btn btn-primary" data-sf-go-dashboard>打开作品列表</button></div></div></div>';
      page.querySelector('[data-sf-go-dashboard]').addEventListener('click', () => go('dashboard'));
      return;
    }
    page.innerHTML = header('StoryFlow 故事画布', `${text(bookName())} · SQLite Story Graph · 当前显示焦点子图`, '<span class="dim-note">Canon 实线 · Planned 虚线 · Candidate 点划线</span>') + `
      <div class="storyflow-page"><div id="sf-toolbar" class="storyflow-toolbar"></div><div class="storyflow-body"><aside id="sf-sidebar" class="storyflow-sidebar"></aside><section class="storyflow-canvas-shell" aria-label="StoryFlow 无限画布"><div id="sf-canvas" class="storyflow-canvas" tabindex="0"></div></section><aside id="sf-inspector" class="storyflow-inspector" aria-label="节点 Inspector"></aside></div></div>`;
    observeModelWork();
    renderToolbar();
    renderSidebar();
    renderInspector();
    await loadGraph();
  };

  window.storyflow = {
    reload: loadGraph,
    focus(id) { if (!state) return; state.focus = id; state.selected = new Set([id]); loadGraph(); },
    open(view, id) {
      if (!state) return;
      state.view = VIEW_META[view] ? view : state.view;
      state.focus = id || '';
      state.selected = id ? new Set([id]) : new Set();
      state.detail = null;
      loadGraph();
    },
  };
  if (typeof S !== 'undefined' && S.page === 'storyflow' && S.book) {
    window.setTimeout(() => go('storyflow'), 0);
  }
}());

// The StoryFlow module is loaded after the base Studio navigation. Re-render
// the navigation once the route becomes available so a first page load does
// not hide the entry until the next refresh.
if (typeof renderNav === 'function') renderNav();
