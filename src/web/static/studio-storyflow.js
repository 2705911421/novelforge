/* global PAGES, S, api, bookName, esc, escAttr, header, go, toast, modal, closeModal */
(function () {
  'use strict';

  const VIEW_META = {
    story: { label: 'Story Flow', sub: '剧情推进', strategy: 'layered' },
    character: { label: '人物视图', sub: '关系与状态', strategy: 'radial' },
    timeline: { label: '时间线', sub: '叙事顺序 × 故事时间', strategy: 'chronological' },
    world: { label: '世界图', sub: '层级世界图 · 势力/驻留/事件叠加', strategy: 'hierarchical' },
    foreshadow: { label: '伏笔生命周期', sub: '埋下 → 推进 → 回收', strategy: 'progression' },
    context: { label: 'Context View', sub: '章节上下文候选', strategy: 'focused' },
    all: { label: 'Full Graph', sub: '显式全图 · bounded', strategy: 'bounded grid' },
  };
  const TYPE_LABEL = {
    Book: '作品', Volume: '卷', Arc: '篇章', Chapter: '章节', Scene: '场景', Event: '事件',
    Character: '人物', Faction: '势力', Location: '地点', World: '世界根节点', Item: '物品', PlotThread: '剧情线',
    Foreshadow: '伏笔', Secret: '秘密', StoryGoal: '故事目标', Conflict: '冲突',
    TimelinePoint: '时间点', StoryBibleEntry: '设定', Knowledge: '知识', Relationship: '关系',
    PlanningNode: '规划', Fact: '事实', StoryState: '故事状态',
  };
  TYPE_LABEL.ContextSource = 'Context Source';
  TYPE_LABEL.PresentationCluster = 'Activity cluster';
  const VIEW_TYPES = {
    story: ['Chapter', 'Scene', 'Event', 'PlotThread', 'Foreshadow', 'Secret', 'StoryGoal', 'Conflict', 'TimelinePoint', 'Item', 'Knowledge', 'Character', 'Faction', 'Location', 'Relationship', 'StoryBibleEntry', 'Fact', 'PlanningNode'],
    character: ['Character', 'Relationship', 'Knowledge', 'Faction', 'Event', 'Location', 'Chapter', 'Fact', 'Foreshadow', 'Scene', 'Item', 'Secret', 'StoryGoal', 'Conflict', 'PlotThread'],
    timeline: ['TimelinePoint', 'Event', 'Chapter', 'Character', 'Location', 'Fact', 'Scene', 'Conflict', 'PlotThread', 'Foreshadow'],
    world: ['World', 'Location', 'Faction', 'Character', 'Event', 'Chapter'],
    foreshadow: ['Foreshadow', 'Chapter', 'Event', 'Character', 'PlotThread', 'Scene', 'Item', 'Secret', 'StoryGoal', 'Conflict', 'Knowledge'],
    context: ['Chapter', 'Character', 'Location', 'Event', 'Foreshadow', 'Fact', 'StoryBibleEntry', 'StoryState', 'Scene', 'Item', 'Secret', 'StoryGoal', 'Conflict', 'TimelinePoint', 'Knowledge'],
    all: ['Book', 'World', 'Volume', 'Arc', 'Chapter', 'Scene', 'Event', 'Character', 'Faction', 'Location', 'Item', 'PlotThread', 'Foreshadow', 'Secret', 'StoryGoal', 'Conflict', 'TimelinePoint', 'StoryBibleEntry', 'Knowledge', 'Relationship', 'PlanningNode', 'Fact', 'StoryState', 'ContextSource'],
  };
  VIEW_TYPES.context.push('PlanningNode', 'ContextSource');
  const TYPE_VIEW = {
    Character: 'character', Relationship: 'character', Knowledge: 'character',
    World: 'world', Location: 'world', Faction: 'world',
    Foreshadow: 'foreshadow',
    TimelinePoint: 'timeline',
    Chapter: 'story', Scene: 'story', Event: 'story', PlotThread: 'story', Secret: 'story', StoryGoal: 'story', Conflict: 'story', TimelinePoint: 'timeline', Item: 'story', StoryBibleEntry: 'story', PlanningNode: 'story',
  };
  TYPE_VIEW.ContextSource = 'context';
  const STATUS_LABEL = { CANON: 'CANON', ACCEPTED: 'ACCEPTED', PLANNED: 'PLANNED', CANDIDATE: 'CANDIDATE', DRAFT: 'DRAFT', SUPERSEDED: 'SUPERSEDED', STALE: 'STALE', CONFLICT: 'CONFLICT' };
  // Dense graphs keep their semantic node DOM (ports, keyboard focus and
  // Inspector navigation) but move the edge paint layer to one 2D surface.
  // This is deliberately a rendering threshold, not a data threshold: the
  // server still owns the complete bounded projection and the same edge
  // records are used for SVG, Canvas paint and hit testing.
  const DENSE_EDGE_THRESHOLD = 40;

  let state = null;

  // Legacy visualization entries are compatibility aliases, not separate
  // data sources.  They set a route intent and enter the same StoryFlow
  // controller after the module has loaded.
  function openStoryFlowView(view, focus = '') {
    const normalizedView = VIEW_META[view] ? view : 'story';
    if (state) {
      // ``go('timeline')`` (and the other compatibility aliases) clears the
      // page before invoking the alias renderer. Re-enter the StoryFlow page
      // when the route changed; otherwise only update the live controller.
      if (typeof S !== 'undefined' && S.page !== 'storyflow') {
        window.__storyflowRouteIntent = { view: normalizedView, focus: focus || '' };
        if (typeof go === 'function') go('storyflow');
        return;
      }
      state.view = normalizedView;
      state.focus = focus || '';
      state.selected = focus ? new Set([focus]) : new Set();
      state.detail = null;
      state.edgeSelectedId = null;
      loadGraph();
      return;
    }
    window.__storyflowRouteIntent = { view: normalizedView, focus: focus || '' };
    if (typeof go === 'function') go('storyflow');
  }

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

  function modelRuntimeStatus() {
    if (!state || state.modelReadinessLoading) return 'checking';
    const readiness = state.modelReadiness;
    if (!readiness) return 'checking';
    if (readiness.ready === true) return 'ready';
    if (readiness.ready === false) return 'setup';
    return 'unavailable';
  }

  function modelRuntimeReady() {
    return modelRuntimeStatus() === 'ready';
  }

  function realNodes() {
    return (state && state.graph && state.graph.nodes || []);
  }

  function presentationModel() {
    const model = state?.graph?.meta?.presentation;
    return model && typeof model === 'object' ? model : null;
  }

  function isPresentationCluster(node) {
    return Boolean(node?.presentationOnly && node?.presentationKind === 'cluster');
  }

  function presentationClusters() {
    const model = presentationModel();
    return Array.isArray(model?.clusters) ? model.clusters : [];
  }

  function clusterPosition(cluster, index) {
    if (!state.presentationClusterPositions) state.presentationClusterPositions = {};
    if (!state.presentationClusterPositions[cluster.id]) {
      state.presentationClusterPositions[cluster.id] = { x: 260 + index * 480, y: 760 };
    }
    return state.presentationClusterPositions[cluster.id];
  }

  function applyPresentationLayout() {
    const model = presentationModel();
    const clusters = presentationClusters();
    if (!model || model.mode !== 'clustered' || !clusters.length) return;
    const source = realNodes();
    const hiddenIds = new Set(model.hiddenNodeIds || []);
    const expanded = state.expandedPresentationClusters || new Set();
    const memberCluster = new Map();
    clusters.forEach((cluster) => (cluster.memberIds || []).forEach((id) => memberCluster.set(id, cluster)));
    const focus = source.find((node) => node.id === state.graph?.focus) || source[0];
    if (focus) {
      if (!focus.layoutSaved) {
        focus.x = 720;
        focus.y = 190;
      }
    }
    const core = source.filter((node) => !node.hidden && !hiddenIds.has(node.id) && node.id !== focus?.id);
    core.sort((left, right) => `${left.type}:${left.title}`.localeCompare(`${right.type}:${right.title}`));
    core.forEach((node, index) => {
      const column = index % 4;
      const row = Math.floor(index / 4);
      if (!node.layoutSaved) {
        node.x = 180 + column * 360;
        node.y = 410 + row * 170;
      }
    });
    clusters.forEach((cluster, index) => {
      const position = clusterPosition(cluster, index);
      const members = source
        .filter((node) => !node.hidden && (cluster.memberIds || []).includes(node.id))
        .sort((left, right) => `${left.type}:${left.title}`.localeCompare(`${right.type}:${right.title}`));
      if (!expanded.has(cluster.id)) {
        members.forEach((node) => {
          if (!node.layoutSaved) {
            node.x = position.x;
            node.y = position.y;
          }
        });
        return;
      }
      members.forEach((node, memberIndex) => {
        if (node.layoutSaved) return;
        const column = memberIndex % 2;
        const row = Math.floor(memberIndex / 2);
        node.x = position.x - 125 + column * 250;
        node.y = 350 + row * 155;
      });
    });
    // Keep the map calculation explicit: every clustered member is still an
    // authoritative node; this map only drives presentation positioning.
    void memberCluster;
  }

  function displayClusterNode(cluster, index) {
    const position = clusterPosition(cluster, index);
    return {
      id: cluster.id,
      type: 'PresentationCluster',
      title: cluster.title,
      summary: cluster.summary,
      status: 'VIEW_ONLY',
      presentationOnly: true,
      presentationKind: 'cluster',
      memberIds: cluster.memberIds || [],
      memberCount: cluster.memberCount || (cluster.memberIds || []).length,
      memberTypes: cluster.memberTypes || {},
      edgeTypeCounts: cluster.edgeTypeCounts || {},
      chapterFrom: cluster.chapterFrom,
      chapterTo: cluster.chapterTo,
      source: cluster.source || 'sqlite.story_graph_projection',
      x: position.x,
      y: position.y,
      collapsed: false,
      hidden: false,
      pinned: false,
      metadata: { presentationOnly: true, source: cluster.source || 'sqlite.story_graph_projection' },
    };
  }

  function canvasNodes() {
    const nodes = realNodes().filter((node) => !node.hidden);
    const model = presentationModel();
    const clusters = presentationClusters();
    if (!model || model.mode !== 'clustered' || !clusters.length) return nodes;
    const hiddenIds = new Set(model.hiddenNodeIds || []);
    const expanded = state.expandedPresentationClusters || new Set();
    const expandedIds = new Set();
    clusters.forEach((cluster) => {
      if (expanded.has(cluster.id)) (cluster.memberIds || []).forEach((id) => expandedIds.add(id));
    });
    const visible = nodes.filter((node) => !hiddenIds.has(node.id) || expandedIds.has(node.id));
    const collapsedClusters = clusters
      .map((cluster, index) => ({ cluster, index }))
      .filter(({ cluster }) => !expanded.has(cluster.id))
      .map(({ cluster, index }) => displayClusterNode(cluster, index));
    return visible.concat(collapsedClusters);
  }

  function collapsedClusterFor(nodeId) {
    const model = presentationModel();
    if (!model || model.mode !== 'clustered') return null;
    const expanded = state.expandedPresentationClusters || new Set();
    return presentationClusters().find((cluster) => !expanded.has(cluster.id) && (cluster.memberIds || []).includes(nodeId)) || null;
  }

  function canvasEdges() {
    if (!state?.graph) return [];
    const nodes = new Map(canvasNodes().map((node) => [node.id, node]));
    const result = [];
    const synthetic = new Map();
    (state.graph.edges || []).forEach((edge) => {
      const sourceCluster = collapsedClusterFor(edge.source);
      const targetCluster = collapsedClusterFor(edge.target);
      const sourceId = sourceCluster?.id || edge.source;
      const targetId = targetCluster?.id || edge.target;
      if (sourceId === targetId) return;
      if (nodes.has(sourceId) && nodes.has(targetId) && !sourceCluster && !targetCluster) {
        result.push(edge);
        return;
      }
      if (!nodes.has(sourceId) || !nodes.has(targetId)) return;
      const key = `${sourceId}->${targetId}`;
      const existing = synthetic.get(key);
      if (existing) {
        existing.metadata.edgeCount += 1;
        existing.metadata.edgeTypes[edge.type] = (existing.metadata.edgeTypes[edge.type] || 0) + 1;
        existing.metadata.sourceEdgeIds.push(edge.id);
        return;
      }
      const grouping = {
        id: `presentation-edge:${sourceId}:${targetId}`,
        source: sourceId,
        target: targetId,
        type: 'presentation_group',
        label: 'Activity evidence',
        status: 'CANON',
        weight: 1,
        confidence: null,
        provenance: [{ kind: 'presentation-only', source: 'sqlite.story_graph_projection' }],
        presentationOnly: true,
        metadata: { edgeCount: 1, edgeTypes: { [edge.type]: 1 }, sourceEdgeIds: [edge.id] },
      };
      synthetic.set(key, grouping);
      result.push(grouping);
    });
    return result;
  }

  function togglePresentationCluster(clusterId) {
    const cluster = presentationClusters().find((item) => item.id === clusterId);
    if (!cluster) return;
    if (!state.expandedPresentationClusters) state.expandedPresentationClusters = new Set();
    const expanding = !state.expandedPresentationClusters.has(clusterId);
    if (expanding) state.expandedPresentationClusters.add(clusterId);
    else state.expandedPresentationClusters.delete(clusterId);
    applyPresentationLayout();
    state.selected = expanding && cluster.memberIds?.length
      ? new Set([cluster.memberIds[0]])
      : new Set([clusterId]);
    state.detail = null;
    renderToolbar();
    renderSidebar();
    renderCanvas();
    renderInspector();
    const selected = selectedNodes()[0];
    if (selected && !isPresentationCluster(selected)) {
      centerOn(selected);
      loadNodeDetail(selected.id);
    }
  }

  function visibleNodes() {
    return canvasNodes();
  }

  function renderedNodes() {
    const nodes = visibleNodes();
    const canvas = document.getElementById('sf-canvas');
    if (!canvas || !state?.transform || !nodes.length) return nodes;
    const rect = canvas.getBoundingClientRect();
    const scale = Math.max(state.transform.scale, 0.01);
    const buffer = 320 / scale;
    const left = (-state.transform.tx / scale) - buffer;
    const top = (-state.transform.ty / scale) - buffer;
    const right = ((rect.width - state.transform.tx) / scale) + buffer;
    const bottom = ((rect.height - state.transform.ty) / scale) + buffer;
    const rendered = nodes.filter((node) => {
      const x = Number(node.x || 0);
      const y = Number(node.y || 0);
      return x >= left && x <= right && y >= top && y <= bottom;
    });
    const important = new Set([
      state.graph?.focus,
      ...state.selected,
    ].filter(Boolean));
    important.forEach((id) => {
      const node = nodes.find((item) => item.id === id);
      if (node && !rendered.includes(node)) rendered.push(node);
    });
    return rendered;
  }

  function renderedEdgeRecords() {
    const nodes = new Map(canvasNodes().map((node) => [node.id, node]));
    const rendered = new Set(renderedNodes().map((node) => node.id));
    return canvasEdges()
      .filter((edge) => rendered.has(edge.source) && rendered.has(edge.target))
      .map((edge) => ({ edge, source: nodes.get(edge.source), target: nodes.get(edge.target) }))
      .filter((item) => item.source && item.target);
  }

  function edgeRendererMode(records = renderedEdgeRecords()) {
    return records.length >= DENSE_EDGE_THRESHOLD ? 'canvas-2d' : 'svg-dom';
  }

  function denseEdgeRendererActive() {
    const canvas = document.getElementById('sf-canvas');
    return canvas?.dataset.edgeRenderer === 'canvas-2d';
  }

  function selectedNodes() {
    const ids = state ? state.selected : new Set();
    return canvasNodes().filter((node) => ids.has(node.id));
  }

  function analysisNodes() {
    return selectedNodes().filter((node) => !isPresentationCluster(node) && node.type !== 'ContextSource');
  }

  function nodeById(id) {
    return canvasNodes().find((node) => node.id === id) || realNodes().find((node) => node.id === id);
  }

  function setSelected(ids, additive) {
    const next = additive ? new Set(state.selected) : new Set();
    ids.forEach((id) => {
      if (additive && next.has(id)) next.delete(id);
      else next.add(id);
    });
    state.selected = next;
    state.edgeSelectedId = null;
    state.edgeHoveredId = null;
    state.detail = null;
    state.impact = null;
    state.chapterImpact = null;
    state.chapterVersionCompare = null;
    state.history = null;
    state.snapshotDiff = null;
    state.canonicalReplay = null;
    state.canonicalDiff = null;
    state.analysisResult = null;
    state.analysisTrace = null;
    state.analysisTaskId = null;
    state.generationRunTrace = null;
    state.generationContextGraph = null;
    state.contextEvidence = null;
    state.candidateComparison = null;
    state.candidateLineage = null;
    state.selectionProjection = null;
    refreshNodeSelection();
    renderToolbar();
    renderSidebar();
    renderInspector();
    const only = selectedNodes()[0];
    if (only && !isPresentationCluster(only)) loadNodeDetail(only.id);
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

  function changeStoryFlowView(view) {
    const nextView = VIEW_META[view] ? view : state.view;
    const targetTypes = new Set(VIEW_TYPES[nextView] || []);
    const selectedInCurrentView = selectedNodes().find((node) => targetTypes.has(node.type));
    const currentFocus = state.focus ? nodeById(state.focus) : null;
    const preservedFocus = currentFocus && targetTypes.has(currentFocus.type)
      ? currentFocus
      : selectedInCurrentView;
    const selectedChapter = selectedNodes().find((node) => node.type === 'Chapter')
      || (currentFocus?.type === 'Chapter' ? currentFocus : null);
    state.view = nextView;
    state.focus = state.view === 'context'
      ? (selectedChapter?.id || state.contextChapterId || '')
      : (preservedFocus?.id || '');
    state.selected = state.focus ? new Set([state.focus]) : new Set();
    state.detail = null;
    state.edgeSelectedId = null;
    state.chapterImpact = null;
    state.chapterVersionCompare = null;
    state.snapshotDiff = null;
    state.canonicalReplay = null;
    state.canonicalDiff = null;
    state.analysisResult = null;
    state.selectionProjection = null;
    state.analysisTrace = null;
    state.analysisTaskId = null;
    state.generationRunTrace = null;
    state.generationContextGraph = null;
    state.candidateComparison = null;
    state.candidateLineage = null;
    if (state.view !== 'context') {
      state.context = null;
      state.contextChapterId = '';
      state.contextEvidence = null;
    }
    loadGraph();
  }

  function planningEditBanner() {
    if (state?.editMode) {
      return '<div class="sf-context-banner sf-mode-banner sf-mode-edit"><b>规划编辑模式</b> · 端口连接和规划写入已启用；AI 分析仍是独立的只读报告任务。这些操作只写入 revisioned planning overlay，不会直接修改 StoryFact 或 StoryState。</div>';
    }
    return '<div class="sf-context-banner sf-mode-banner"><b>只读模式</b> · Canon 事实和语义边不可从画布直接修改；AI 分析可以读取选中真实节点并持久化为报告任务，不会写入 StoryFact、StoryState 或 StoryCommit。节点位置、折叠和隐藏仍属于独立的 UI workspace state。</div>';
  }

  function requirePlanningEditMode() {
    if (state?.editMode) return true;
    toast('当前是只读模式。切换到“规划编辑”后，才能创建 PLANNED 连接或写入规划任务。', 'warning');
    return false;
  }

  function togglePlanningEditMode() {
    if (!state) return;
    state.editMode = !state.editMode;
    if (!state.editMode) {
      if (state.connection) stopPortDrag();
      hideEdgeChooser();
    }
    renderToolbar();
    renderSidebar();
    renderCanvas();
    renderInspector();
    toast(state.editMode ? '已进入规划编辑模式：可创建 PLANNED 连接和规划任务。' : '已回到只读模式：Canon 事实保持安全。', 'success');
  }

  function renderToolbar() {
    const toolbar = document.getElementById('sf-toolbar');
    if (!toolbar) return;
    const editMode = !!state.editMode;
    const planningWriteAttrs = editMode ? '' : 'disabled aria-disabled="true"';
    const modelActionAttrs = modelRuntimeReady() ? '' : 'disabled aria-disabled="true"';
    toolbar.innerHTML = `
      <div class="sf-toolbar-group">
        <label class="sr-only" for="sf-view-select">StoryFlow 视图</label>
        <select id="sf-view-select" class="sf-view-select" aria-label="StoryFlow 视图">
          ${Object.entries(VIEW_META).map(([key, meta]) => `<option value="${key}" ${state.view === key ? 'selected' : ''}>${text(meta.label)}</option>`).join('')}
        </select>
        <span class="sf-toolbar-caption">${text(VIEW_META[state.view]?.strategy || '')}</span>
      </div>
      ${(state.view === 'character' || state.view === 'story' || state.view === 'all') ? `<button class="btn btn-sm btn-secondary sf-presentation-toggle" data-sf-action="toggle-presentation" aria-pressed="${state.presentationMode === 'clustered' ? 'true' : 'false'}">${state.presentationMode === 'clustered' ? 'Activity clusters' : 'All evidence nodes'}</button>` : ''}
      ${state.view === 'all' ? '<span class="sf-bounded-badge" title="Full Graph is explicit and bounded by the Graph API limit">FULL GRAPH · BOUNDED</span>' : ''}
      <button class="btn btn-sm sf-mode-toggle ${editMode ? 'is-editing' : 'is-readonly'}" data-sf-action="toggle-edit-mode" aria-pressed="${editMode ? 'true' : 'false'}" title="${editMode ? '回到只读模式；规划写入将停止' : '进入规划编辑模式；只允许写入 revisioned planning overlay'}">${editMode ? '规划编辑' : '只读 · Canon'}</button>
      <div class="sf-spacer"></div>
      <div class="sf-search-wrap">
        <input id="sf-search" class="sf-search-input" type="search" autocomplete="off" placeholder="搜索人物、章节、伏笔…" aria-label="搜索故事图">
        <div id="sf-search-results" class="sf-search-results" hidden></div>
      </div>
      <button class="btn btn-sm btn-secondary" data-sf-action="auto-layout" title="按当前视图重新布局">自动布局</button>
      <button class="btn btn-sm btn-secondary" data-sf-action="save-layout" title="保存当前工作区位置">保存布局</button>
      <button class="btn btn-sm btn-ghost" data-sf-action="undo-layout" title="撤销最近一次布局保存（Ctrl/Cmd+Z）" ${state.layoutHistory?.canUndo ? '' : 'disabled'}>撤销</button>
      <button class="btn btn-sm btn-ghost" data-sf-action="redo-layout" title="恢复下一次布局保存（Ctrl/Cmd+Shift+Z）" ${state.layoutHistory?.canRedo ? '' : 'disabled'}>重做</button>
    `;
    if (state.view === 'all' && state.graph?.meta?.viewport?.requested) {
      const viewport = state.graph.meta.viewport;
      if (viewport.hasMore && viewport.nextPageToken) {
        toolbar.insertAdjacentHTML('afterbegin', '<button type="button" class="sf-viewport-next" data-sf-action="load-next-viewport-page">Load next viewport page</button>');
      }
      if (viewport.internalEdgesTruncated && viewport.nextInternalEdgePageToken) {
        toolbar.insertAdjacentHTML('afterbegin', '<button type="button" class="sf-viewport-next sf-edge-next" data-sf-action="load-next-viewport-edge-page">Load more semantic edges</button>');
      }
      const loaded = Number(viewport.loadedNodeCount || state.graph.nodes?.length || 0);
      const total = Number(state.graph.meta.totalAvailableNodes || 0);
      const loadedLabel = total ? `${loaded} loaded / ${total} total` : `${loaded} loaded`;
      const mergeLabel = viewport.incrementalMerge ? ' · incremental' : '';
      const boundaryCount = Number(viewport.crossBoundaryEdgeCount || 0);
      const boundaryLabel = boundaryCount
        ? ` · ${boundaryCount} boundary edge${boundaryCount === 1 ? '' : 's'}`
        : '';
      const internalEdgeCount = Number(viewport.internalEdgeCount || 0);
      const loadedInternalEdges = Number(viewport.loadedInternalEdgeCount || viewport.returnedInternalEdges || 0);
      const internalEdgeLabel = internalEdgeCount
        ? ` · ${loadedInternalEdges}/${internalEdgeCount} semantic edges`
        : '';
      toolbar.insertAdjacentHTML('afterbegin', `<span class="sf-bounded-badge sf-viewport-badge" title="Server-side world-coordinate viewport projection; node and semantic-edge pages are merged into the current read model. Boundary edges remain queryable from the selected node Inspector">VIEWPORT · ${text(loadedLabel)}${text(internalEdgeLabel)}${text(mergeLabel)}${text(boundaryLabel)}</span>`);
    }
    if (state.view === 'all' && state.viewportFetchError) {
      toolbar.insertAdjacentHTML('afterbegin', `<span class="sf-bounded-badge sf-viewport-badge is-error" title="${attr(state.viewportFetchError)}">VIEWPORT query error</span>`);
    }
    if (state.graphFreshness?.changed) {
      const pending = state.graphFreshness.resyncRequired || state.editMode || state.connection || state.layoutDirty;
      toolbar.insertAdjacentHTML('afterbegin', `<span class="sf-bounded-badge sf-freshness-badge ${pending ? 'is-pending' : ''}" title="${attr(state.graphFreshness.reason || 'A newer observed Story Graph projection is available')}" data-sf-freshness-banner>CANON UPDATE${pending ? ' · REFRESH REQUIRED' : ''}<button type="button" data-sf-action="refresh-graph">Refresh</button></span>`);
    } else if (state.graphFreshnessError) {
      toolbar.insertAdjacentHTML('afterbegin', `<span class="sf-bounded-badge sf-freshness-badge is-error" title="${attr(state.graphFreshnessError)}">FRESHNESS CHECK FAILED</span>`);
    }
    if (state.modelReadinessLoading || state.modelReadiness) {
      const runtimeStatus = modelRuntimeStatus();
      const readiness = state.modelReadiness || {};
      const runtimeLabel = runtimeStatus === 'ready'
        ? 'AI RUNTIME · READY'
        : runtimeStatus === 'setup'
          ? 'AI RUNTIME · SETUP REQUIRED'
          : runtimeStatus === 'unavailable'
            ? 'AI RUNTIME · UNAVAILABLE'
            : 'AI RUNTIME · CHECKING';
      const runtimeAction = runtimeStatus === 'setup'
        ? '<button type="button" data-sf-action="open-model-config">Open AI config</button>'
        : runtimeStatus === 'unavailable'
          ? '<button type="button" data-sf-action="refresh-model-readiness">Retry</button>'
          : '';
      toolbar.insertAdjacentHTML('afterbegin', `<span class="sf-bounded-badge sf-runtime-badge ${runtimeStatus === 'ready' ? 'is-ready' : runtimeStatus === 'setup' ? 'is-setup' : 'is-pending'}" title="${attr(readiness.message || 'StoryFlow model runtime readiness')}">${runtimeLabel}${runtimeAction}</span>`);
    }
    const analysisAttrs = analysisNodes().length && modelRuntimeReady() ? '' : 'disabled aria-disabled="true"';
    toolbar.insertAdjacentHTML('beforeend', `<button class="btn btn-sm btn-secondary" data-sf-action="create-planning-node" title="在 revisioned planning overlay 中创建一个作者规划节点" ${planningWriteAttrs}>新建规划节点</button><button class="btn btn-sm btn-secondary" data-sf-action="generate-intent" title="将选中 Story Flow 保存为章节计划" ${planningWriteAttrs}>保存章节计划</button><button class="btn btn-sm btn-primary" data-sf-action="generate-chapter" title="将选中 Flow 保存为计划并排队生成下一章" ${planningWriteAttrs}>生成章节</button><button class="btn btn-sm btn-secondary" data-sf-action="generate-candidates" title="通过持久模型任务生成候选分支" ${planningWriteAttrs}>生成候选分支</button><button class="btn btn-sm btn-secondary" data-sf-action="analyze-selection" title="读取选中的真实 Story Graph 节点并持久化只读分析报告；不修改 Canon" ${analysisAttrs}>AI 分析选择</button><button class="btn btn-sm btn-ghost" data-sf-action="adopt-candidate" title="将选中候选纳入计划" ${planningWriteAttrs}>采用候选</button><button class="btn btn-sm btn-ghost" data-sf-action="discard-candidate" title="将选中候选标记为废弃" ${planningWriteAttrs}>丢弃候选</button>`);
    if (!modelRuntimeReady()) {
      ['generate-chapter', 'generate-candidates'].forEach((action) => {
        const button = toolbar.querySelector(`[data-sf-action="${action}"]`);
        if (button) {
          button.disabled = true;
          button.setAttribute('aria-disabled', 'true');
        }
      });
    }
    toolbar.querySelector('#sf-view-select').addEventListener('change', (event) => {
      changeStoryFlowView(event.target.value);
    });
    toolbar.querySelector('[data-sf-action="toggle-edit-mode"]').addEventListener('click', togglePlanningEditMode);
    toolbar.querySelector('[data-sf-action="toggle-presentation"]')?.addEventListener('click', () => {
      state.presentationMode = state.presentationMode === 'clustered' ? 'expanded' : 'clustered';
      state.expandedPresentationClusters = new Set();
      loadGraph();
    });
    toolbar.querySelector('[data-sf-action="create-planning-node"]').addEventListener('click', createPlanningNode);
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
    toolbar.querySelector('[data-sf-action="generate-chapter"]').addEventListener('click', generateChapterFromSelection);
    toolbar.querySelector('[data-sf-action="generate-candidates"]').addEventListener('click', generateCandidateBranches);
    toolbar.querySelector('[data-sf-action="analyze-selection"]').addEventListener('click', analyzeSelection);
    toolbar.querySelector('[data-sf-action="adopt-candidate"]').addEventListener('click', () => decideCandidate('adopt'));
    toolbar.querySelector('[data-sf-action="discard-candidate"]').addEventListener('click', () => decideCandidate('discard'));
    toolbar.querySelector('[data-sf-action="auto-layout"]').addEventListener('click', autoLayout);
    toolbar.querySelector('[data-sf-action="save-layout"]').addEventListener('click', saveLayout);
    toolbar.querySelector('[data-sf-action="undo-layout"]').addEventListener('click', undoLayout);
    toolbar.querySelector('[data-sf-action="redo-layout"]').addEventListener('click', redoLayout);
    toolbar.querySelector('[data-sf-action="load-next-viewport-page"]')?.addEventListener('click', loadNextViewportPage);
    toolbar.querySelector('[data-sf-action="load-next-viewport-edge-page"]')?.addEventListener('click', loadNextViewportEdgePage);
    toolbar.querySelector('[data-sf-action="refresh-graph"]')?.addEventListener('click', refreshGraphFromCanon);
    toolbar.querySelector('[data-sf-action="open-model-config"]')?.addEventListener('click', () => go('agent-config'));
    toolbar.querySelector('[data-sf-action="refresh-model-readiness"]')?.addEventListener('click', loadModelReadiness);
  }

  function candidateBranchById(branchId) {
    for (const candidateSet of state?.candidateSets || []) {
      const branch = (candidateSet.branches || []).find((item) => item.candidateBranchId === branchId);
      if (branch) return branch;
    }
    return null;
  }

  function renderCandidateSetsSection() {
    const candidateSets = Array.isArray(state.candidateSets) ? state.candidateSets : [];
    if (state.candidateSetsLoading && !candidateSets.length) {
      return '<div class="sf-filter-block sf-candidate-section"><div class="sf-panel-title"><span>候选分支</span><small>读取中</small></div><p class="dim-note">正在读取 revisioned planning overlay…</p></div>';
    }
    if (state.candidateSetsError) {
      return `<div class="sf-filter-block sf-candidate-section"><div class="sf-panel-title"><span>候选分支</span><small>读取失败</small></div><div class="sf-candidate-error">${text(state.candidateSetsError)}</div><p class="dim-note">来源：SQLite plot_workspaces；错误不会被静默吞掉。</p></div>`;
    }
    if (!candidateSets.length) {
      return '<div class="sf-filter-block sf-candidate-section"><div class="sf-panel-title"><span>候选分支</span><small>0 sets</small></div><p class="dim-note">当前没有已持久化的候选集合。AI 推演完成后，分支会以 CANDIDATE 覆盖层出现在这里。</p></div>';
    }
    const writeAttrs = state.editMode ? '' : 'disabled aria-disabled="true"';
    const sets = candidateSets.slice(0, 8).map((candidateSet) => {
      const branches = Array.isArray(candidateSet.branches) ? candidateSet.branches : [];
      const activeBranches = branches.filter((branch) => branch.status === 'CANDIDATE');
      const source = candidateSet.sourceTaskId || candidateSet.generationRunId || '未关联运行';
      const analysisSource = candidateSet.sourceAnalysisTaskId ? ` · derived from ${candidateSet.sourceAnalysisTaskId}` : '';
      const parentSource = candidateSet.sourceCandidateBranchId ? ` · from branch ${candidateSet.sourceCandidateBranchId}` : '';
      return `<section class="sf-candidate-set" data-sf-candidate-set="${attr(candidateSet.candidateSetId)}">
        <div class="sf-candidate-set-head"><span><b>${text(candidateSet.originTitle || '未命名起点')}</b><small>${text(source + analysisSource + parentSource)}</small></span><span class="sf-status-badge ${statusClass(candidateSet.status)}">${text(candidateSet.status)} · ${text(candidateSet.branchCount)} 方案</span></div>
        <div class="sf-candidate-branch-list">${branches.map((branch) => {
          const candidate = branch.status === 'CANDIDATE';
          const decisionLabel = branch.status === 'PLANNED' ? '已采用' : branch.status === 'SUPERSEDED' ? '已丢弃' : '候选';
          return `<div class="sf-candidate-branch-row" data-sf-candidate-branch="${attr(branch.candidateBranchId)}" tabindex="0" role="button" aria-label="聚焦候选方案 ${attr(branch.title)}">
            <div class="sf-candidate-branch-main"><span class="sf-candidate-branch-index">${text(branch.branchIndex)}.</span><span><b>${text(branch.title)}</b><small>${text(branch.summary || branch.plotPoints?.[0] || '未提供摘要')}</small></span></div>
            <div class="sf-candidate-branch-meta"><span class="sf-status-badge ${statusClass(branch.status)}">${text(decisionLabel)}</span>${branch.score != null ? `<span class="sf-candidate-score">${text(branch.score)}</span>` : ''}</div>
            ${candidate ? `<div class="sf-candidate-branch-actions"><button class="btn btn-sm btn-secondary" data-sf-candidate-decision="adopt" data-sf-candidate-branch-id="${attr(branch.candidateBranchId)}" ${writeAttrs}>采用</button><button class="btn btn-sm btn-ghost" data-sf-candidate-decision="discard" data-sf-candidate-branch-id="${attr(branch.candidateBranchId)}" ${writeAttrs}>丢弃</button></div>` : ''}
          </div>`;
        }).join('')}</div>
        <div class="sf-candidate-set-actions"><button class="btn btn-sm btn-secondary" data-sf-candidate-compare="${attr(candidateSet.candidateSetId)}" ${branches.length >= 2 ? '' : 'disabled aria-disabled="true"'}>比较方案</button></div>
        ${activeBranches.length ? `<div class="sf-candidate-set-actions"><button class="btn btn-sm btn-ghost" data-sf-candidate-set-discard="${attr(candidateSet.candidateSetId)}" ${writeAttrs}>全部丢弃</button>${state.editMode ? '' : '<small>切换规划编辑后可决策</small>'}</div>` : ''}
      </section>`;
    }).join('');
    const overflow = candidateSets.length > 8 ? `<p class="dim-note">仅显示前 8 个集合；可通过候选 API 按 sourceTaskId 或 candidateSetId 查询。</p>` : '';
    return `<div class="sf-filter-block sf-candidate-section"><div class="sf-panel-title"><span>候选分支</span><small>${text(candidateSets.length)} sets · SQLite overlay</small></div>${sets}${overflow}<p class="dim-note">同一候选集合来自一次推演运行；采用/丢弃只改变 PLANNED / SUPERSEDED 规划状态。</p></div>`;
  }

  function renderForecastRecoverySection() {
    const tasks = Array.isArray(state.recoverableForecastTasks) ? state.recoverableForecastTasks : [];
    if (state.recoverableForecastTasksLoading && !tasks.length) {
      return '<div class="sf-filter-block sf-candidate-section"><div class="sf-panel-title"><span>Recoverable forecasts</span><small>loading</small></div><p class="dim-note">Checking durable forecast task results.</p></div>';
    }
    if (state.recoverableForecastTasksError) {
      return `<div class="sf-filter-block sf-candidate-section"><div class="sf-panel-title"><span>Recoverable forecasts</span><small>read failed</small></div><div class="sf-candidate-error">${text(state.recoverableForecastTasksError)}</div><p class="dim-note">Source: SQLite tasks + plot_workspaces. Recovery errors stay visible.</p></div>`;
    }
    if (!tasks.length) return '';
    const writeAttrs = state.editMode ? '' : 'disabled aria-disabled="true"';
    return `<div class="sf-filter-block sf-candidate-section sf-forecast-recovery-section"><div class="sf-panel-title"><span>Recoverable forecasts</span><small>${text(tasks.length)} tasks</small></div>${tasks.map((task) => `
      <div class="sf-forecast-recovery-row">
        <div class="sf-forecast-recovery-main"><b>${text(task.candidateSetId || task.taskId)}</b><small>${text(task.branchCount)} branches · ${text(task.importStatus || 'unimported')}</small>${task.importError ? `<span>${text(task.importError)}</span>` : ''}</div>
        <button class="btn btn-sm btn-secondary" data-sf-recover-forecast="${attr(task.taskId)}" data-sf-recover-source="${attr(task.sourceNodeId || '')}" ${writeAttrs}${state.recoveringForecastTaskId === task.taskId ? ' disabled' : ''}>${state.recoveringForecastTaskId === task.taskId ? 'Recovering…' : 'Recover candidates'}</button>
      </div>`).join('')}<p class="dim-note">Recovery writes only planning overlay and audit rows; Canon remains unchanged.</p></div>`;
  }

  function renderSidebar() {
    const sidebar = document.getElementById('sf-sidebar');
    if (!sidebar) return;
    const graph = state.graph || { nodes: [], edges: [], meta: {} };
    const health = graph.meta?.projectionHealth || { status: 'HEALTHY', staleNodes: [], conflictNodes: [] };
    const typeCounts = graph.nodes.reduce((counts, node) => {
      counts[node.type] = (counts[node.type] || 0) + 1;
      return counts;
    }, {});
    const types = VIEW_TYPES[state.view] || [];
    const volumes = Array.isArray(graph.meta.availableVolumes) ? graph.meta.availableVolumes : [];
    const presentation = graph.meta.presentation || {};
    const displayNodes = canvasNodes();
    const hiddenNodes = realNodes()
      .filter((node) => node.hidden)
      .sort((left, right) => `${left.type}:${left.title}`.localeCompare(`${right.type}:${right.title}`));
    const presentationNotice = (state.view === 'character' || state.view === 'story' || state.view === 'all') && presentation.mode === 'clustered'
      ? `<div class="sf-presentation-summary"><b>Progressive disclosure</b><span>${text(presentation.sourceNodeCount || graph.meta.returnedNodes || 0)} real nodes → ${text(displayNodes.length)} displayed</span><small>${text((presentation.clusters || []).length)} activity groups are view-only aggregates. Canon nodes remain in SQLite.</small></div>`
      : state.view === 'all'
        ? `<div class="sf-full-graph-notice"><b>Full Graph is explicit, not the default</b><span>当前请求最多返回 ${text(graph.meta.returnedNodes || 0)} / ${text(graph.meta.totalAvailableNodes || 0)} 个真实节点和 ${text(graph.meta.returnedEdges || 0)} 条语义边。</span><small>${graph.meta.truncated ? '结果已按 Graph API 上限截断；可继续用搜索、Focus 和 Depth 读取局部子图。' : '当前结果在本次有界查询内完整；仍建议用 Focus/Depth 处理长篇作品。'}</small></div>`
        : '';
    sidebar.innerHTML = `
      <div class="sf-panel-title"><span>Story Views</span><small>${text(VIEW_META[state.view]?.sub || '')}</small></div>
      ${presentationNotice}
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
          <option value="">全部</option>${['CANON', 'ACCEPTED', 'PLANNED', 'CANDIDATE', 'DRAFT', 'STALE', 'CONFLICT'].map((status) => `<option value="${status}" ${state.statuses.includes(status) ? 'selected' : ''}>${status}</option>`).join('')}
        </select></div>
        <div class="sf-filter-row"><label>章节范围</label><div class="sf-filter-range"><input id="sf-chapter-from" class="sf-filter-input" inputmode="numeric" placeholder="起" value="${attr(state.chapterFrom || '')}"><span>–</span><input id="sf-chapter-to" class="sf-filter-input" inputmode="numeric" placeholder="止" value="${attr(state.chapterTo || '')}"></div></div>
        <div class="sf-filter-row"><label for="sf-volume-filter">卷</label><select id="sf-volume-filter" class="sf-filter-select"><option value="">全部卷</option>${volumes.map((volume) => `<option value="${attr(volume.number)}" ${Number(state.volumeNumber) === Number(volume.number) ? 'selected' : ''}>第${text(volume.number)}卷 · ${text(volume.title)}</option>`).join('')}</select></div>
        <div class="sf-filter-row"><label>故事时间</label><div class="sf-filter-range"><input id="sf-time-from" class="sf-filter-input" placeholder="起" value="${attr(state.timeFrom || '')}"><span>–</span><input id="sf-time-to" class="sf-filter-input" placeholder="止" value="${attr(state.timeTo || '')}"></div></div>
        <div class="sf-filter-row"><label for="sf-plot-thread">剧情线</label><input id="sf-plot-thread" class="sf-filter-input sf-filter-wide" placeholder="名称或 ID" value="${attr(state.plotThread || '')}"></div>
        <div class="sf-panel-title" style="margin-top:12px"><span>节点类型</span><small>可多选</small></div>
        <div class="sf-type-list">${types.map((type) => `<button class="sf-type-chip ${state.types.includes(type) ? 'is-active' : ''}" data-sf-type="${type}">${text(nodeLabel(type))} ${typeCounts[type] ? `<span>${typeCounts[type]}</span>` : ''}</button>`).join('')}</div>
      </div>
      <div class="sf-filter-block">
        <div class="sf-panel-title"><span>当前子图</span><small>${graph.meta.focused ? 'focused' : 'bounded'}</small></div>
        <div class="sf-graph-stats">
          <div class="sf-stat"><b>${text(displayNodes.length)}</b><span>displayed nodes</span></div>
          <div class="sf-stat"><b>${text(graph.meta.returnedEdges || 0)}</b><span>语义边</span></div>
          <div class="sf-stat"><b>${text(state.depth)}</b><span>展开层级</span></div>
          <div class="sf-stat"><b>${graph.meta.truncated ? '是' : '否'}</b><span>有截断</span></div>
        </div>
         <p class="dim-note" style="margin:9px 2px 0;font-size:10px">事实来源：${text(graph.meta.canonicalSource || 'sqlite')} · 布局属于工作区状态</p>
         ${state.view === 'world' && graph.meta.worldGraph ? `<div class="sf-world-graph-note"><b>World Graph</b><span>World → Region → City → Location</span><small>无坐标时只展示层级关系；控制、人物驻留、事件使用 SQLite 状态叠加。</small></div>` : ''}
         ${health.status !== 'HEALTHY' ? `<div class="sf-health-banner ${health.status === 'CONFLICT' ? 'is-conflict' : 'is-stale'}"><b>${text(health.status)}</b><span>${text((health.conflictNodes || []).length)} conflict · ${text((health.staleNodes || []).length)} stale</span><small>投影诊断来自 StoryCommit / Review；不会绕过 Canon 边界。</small></div>` : ''}
       </div>
       ${renderStoryHealthSection()}
       ${hiddenNodes.length ? `<div class="sf-filter-block sf-hidden-node-section"><div class="sf-panel-title"><span>Hidden workspace nodes</span><small>${text(hiddenNodes.length)} hidden</small></div><div class="sf-node-list">${hiddenNodes.slice(0, 80).map((node) => `<div class="sf-hidden-node-row"><button class="sf-neighbor-row" data-sf-select-hidden="${attr(node.id)}" title="${attr(`Restore ${node.title}`)}"><span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><small style="color:var(--text-muted)">${text(nodeLabel(node.type))}</small> ${text(node.title)}</span><span class="sf-neighbor-edge">HIDDEN</span></button><button class="btn btn-sm btn-ghost sf-restore-hidden" data-sf-restore-hidden="${attr(node.id)}">Restore</button></div>`).join('')}</div>${hiddenNodes.length > 80 ? '<p class="dim-note">Showing the first 80 hidden workspace nodes.</p>' : ''}<p class="dim-note">Hidden is workspace state only. Restoring a node never writes StoryFact or StoryState.</p></div>` : ''}
       <div class="sf-filter-block">
         <div class="sf-panel-title"><span>节点清单</span><small>点击定位</small></div>
         <div class="sf-node-list">${displayNodes.slice(0, 80).map((node) => `<button class="sf-neighbor-row" data-sf-select="${attr(node.id)}"><span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><small style="color:var(--text-muted)">${text(nodeLabel(node.type))}</small> ${text(node.title)}</span><span class="sf-neighbor-edge">${text(isPresentationCluster(node) ? 'VIEW ONLY' : statusLabel(node.status))}</span></button>`).join('') || '<p class="dim-note">当前焦点没有可显示节点。</p>'}</div>
       </div>
       ${renderCandidateSetsSection()}
       ${renderForecastRecoverySection()}
       ${state.analysisHistory?.length ? `<div class="sf-filter-block"><div class="sf-panel-title"><span>最近 AI 分析</span><small>durable tasks</small></div><div class="sf-analysis-history-list">${state.analysisHistory.slice(0, 6).map((task) => `<button class="sf-analysis-history-row" data-sf-analysis-task="${attr(task.taskId)}"><span><b>${text(task.status)}</b><small>${text((task.nodeIds || []).join(', '))}</small></span><span>${text(task.createdAt || '—')}</span></button>`).join('')}</div><p class="dim-note">报告读取自 tasks.result；刷新后仍可恢复，不会写入 StoryFact。</p></div>` : ''}
     `;
    sidebar.querySelectorAll('[data-sf-view]').forEach((button) => button.addEventListener('click', () => {
      changeStoryFlowView(button.dataset.sfView);
    }));
    sidebar.querySelectorAll('[data-sf-depth]').forEach((button) => button.addEventListener('click', () => {
      state.depth = Number(button.dataset.sfDepth);
      loadGraph();
    }));
    sidebar.querySelectorAll('[data-sf-type]').forEach((button) => button.addEventListener('click', () => {
      const type = button.dataset.sfType;
      state.types = state.types.includes(type) ? state.types.filter((item) => item !== type) : [...state.types, type];
      resetFocusAfterFilter();
      loadGraph();
    }));
    sidebar.querySelector('#sf-status-filter').addEventListener('change', (event) => {
      state.statuses = event.target.value ? [event.target.value] : [];
      resetFocusAfterFilter();
      loadGraph();
    });
    const applyRange = () => {
      const from = sidebar.querySelector('#sf-chapter-from').value.trim();
      const to = sidebar.querySelector('#sf-chapter-to').value.trim();
      state.chapterFrom = from && /^\d+$/.test(from) ? Number(from) : '';
      state.chapterTo = to && /^\d+$/.test(to) ? Number(to) : '';
      resetFocusAfterFilter();
      loadGraph();
    };
    sidebar.querySelector('#sf-chapter-from').addEventListener('change', applyRange);
    sidebar.querySelector('#sf-chapter-to').addEventListener('change', applyRange);
    sidebar.querySelector('#sf-volume-filter').addEventListener('change', (event) => {
      state.volumeNumber = event.target.value ? Number(event.target.value) : '';
      resetFocusAfterFilter();
      loadGraph();
    });
    const applyTimeAndPlot = () => {
      state.timeFrom = sidebar.querySelector('#sf-time-from').value.trim();
      state.timeTo = sidebar.querySelector('#sf-time-to').value.trim();
      state.plotThread = sidebar.querySelector('#sf-plot-thread').value.trim();
      resetFocusAfterFilter();
      loadGraph();
    };
    sidebar.querySelector('#sf-time-from').addEventListener('change', applyTimeAndPlot);
    sidebar.querySelector('#sf-time-to').addEventListener('change', applyTimeAndPlot);
    sidebar.querySelector('#sf-plot-thread').addEventListener('change', applyTimeAndPlot);
    sidebar.querySelectorAll('[data-sf-select]').forEach((button) => button.addEventListener('click', () => {
      const id = button.dataset.sfSelect;
      const node = nodeById(id);
      if (!node) return;
      state.selected = new Set([id]);
      state.focus = isPresentationCluster(node) ? (state.graph.focus || '') : id;
      refreshNodeSelection();
      renderInspector();
      if (!isPresentationCluster(node)) loadNodeDetail(id);
      centerOn(node);
    }));
    sidebar.querySelectorAll('[data-sf-select-hidden]').forEach((button) => button.addEventListener('click', () => {
      const id = button.dataset.sfSelectHidden;
      const node = nodeById(id);
      if (!node) return;
      restoreHiddenNode(node);
    }));
    sidebar.querySelectorAll('[data-sf-restore-hidden]').forEach((button) => button.addEventListener('click', (event) => {
      event.stopPropagation();
      const node = nodeById(button.dataset.sfRestoreHidden);
      if (node) restoreHiddenNode(node);
    }));
    sidebar.querySelectorAll('[data-sf-health-focus]').forEach((button) => button.addEventListener('click', () => {
      focusStoryHealth(button.dataset.sfHealthFocus, button.dataset.sfHealthType);
    }));
    sidebar.querySelectorAll('[data-sf-candidate-branch]').forEach((row) => {
      const focus = () => focusCandidateBranch(row.dataset.sfCandidateBranch);
      row.addEventListener('click', (event) => {
        if (event.target.closest('button')) return;
        focus();
      });
      row.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          focus();
        }
      });
    });
    sidebar.querySelectorAll('[data-sf-candidate-decision]').forEach((button) => button.addEventListener('click', (event) => {
      event.stopPropagation();
      decideCandidateBranch(button.dataset.sfCandidateBranchId, button.dataset.sfCandidateDecision);
    }));
    sidebar.querySelectorAll('[data-sf-candidate-set-discard]').forEach((button) => button.addEventListener('click', (event) => {
      event.stopPropagation();
      decideCandidateSet(button.dataset.sfCandidateSet, 'discard');
    }));
    sidebar.querySelectorAll('[data-sf-candidate-compare]').forEach((button) => button.addEventListener('click', (event) => {
      event.stopPropagation();
      loadCandidateComparison(button.dataset.sfCandidateCompare);
    }));
    sidebar.querySelectorAll('[data-sf-recover-forecast]').forEach((button) => button.addEventListener('click', (event) => {
      event.stopPropagation();
      recoverForecastTask(button.dataset.sfRecoverForecast, button.dataset.sfRecoverSource || '');
    }));
    sidebar.querySelectorAll('[data-sf-analysis-task]').forEach((button) => button.addEventListener('click', () => restoreAnalysisTask(button.dataset.sfAnalysisTask)));
  }

  function resetFocusAfterFilter() {
    // A previous node focus is a user navigation state, not a filter. Keeping
    // it after a filter change makes the API re-inject an excluded node and
    // can make a correct filtered subgraph look empty. Let the projector pick
    // the first real node in the filtered candidates instead.
    state.focus = '';
    state.selected = new Set();
    state.edgeSelectedId = null;
    state.detail = null;
    state.impact = null;
    state.chapterImpact = null;
    state.chapterVersionCompare = null;
    state.history = null;
    state.canonicalReplay = null;
    state.canonicalDiff = null;
  }

  function restoreHiddenNode(node) {
    if (!node) return;
    node.hidden = false;
    state.layoutDirty = true;
    state.focus = node.id;
    state.depth = Math.max(1, Math.min(3, Number(state.depth) || 1));
    state.selected = new Set([node.id]);
    state.detail = null;
    state.edgeSelectedId = null;
    // Keep this local until the author explicitly saves the layout. Reloading
    // here would re-apply the last persisted `hidden=true` workspace record
    // and make the restore action appear to do nothing.
    renderToolbar();
    renderSidebar();
    renderCanvas();
    renderInspector();
    centerOn(node);
    loadNodeDetail(node.id);
  }

  function hideWorkspaceNodes(nodes) {
    const hidden = (nodes || []).filter(Boolean);
    if (!hidden.length) return;
    hidden.forEach((node) => {
      node.hidden = true;
      state.selected.delete(node.id);
      if (state.focus === node.id) state.focus = '';
    });
    state.layoutDirty = true;
    state.detail = null;
    state.edgeSelectedId = null;
    renderToolbar();
    renderSidebar();
    renderCanvas();
    renderInspector();
  }

  function renderCanvas() {
    const canvas = document.getElementById('sf-canvas');
    if (!canvas) return;
    if (!state.canvasObserver && typeof ResizeObserver !== 'undefined') {
      state.canvasObserver = new ResizeObserver(() => {
        if (!state?.graph) return;
      const focused = state.focus && selectedNodes()[0];
        if (focused) centerOn(focused);
        else fitGraph();
        resizeEdgeCanvas();
        if (denseEdgeRendererActive()) drawDenseEdges(renderedEdgeRecords());
      });
      state.canvasObserver.observe(canvas);
    }
    canvas.dataset.storyflowMode = state.editMode ? 'planning-edit' : 'read-only';
    canvas.classList.toggle('is-planning-edit', !!state.editMode);
    canvas.classList.toggle('is-read-only', !state.editMode);
    const graph = state.graph || { nodes: [], edges: [] };
    if (!canvasNodes().length) {
      canvas.innerHTML = '<div class="sf-canvas-empty"><div><strong>当前视图没有可显示的故事事实</strong><span>尝试降低过滤条件，或先在章节工作台建立真实内容。</span></div></div>';
      return;
    }
    canvas.innerHTML = `
      <canvas id="sf-edge-canvas" class="sf-edge-canvas" aria-label="故事语义边 Canvas 渲染层"></canvas>
      <svg id="sf-edge-layer" class="sf-edge-layer" aria-label="故事语义连线"><defs><marker id="sf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#f4a261"></path></marker></defs><g id="sf-edge-group"></g></svg>
      <div id="sf-world" class="sf-world"></div>
      ${state.view === 'timeline' ? '<div class="sf-timeline-axes" aria-label="Timeline axes"><span>← Narrative Order · 叙事顺序</span><span>Story Time · 故事时间 ↓</span></div>' : ''}
      <div class="sf-canvas-controls"><button data-sf-canvas="zoom-out" title="缩小">−</button><span id="sf-zoom-label" class="sf-zoom-label">100%</span><button data-sf-canvas="zoom-in" title="放大">+</button><button data-sf-canvas="fit" title="适合画布">适</button><button data-sf-canvas="reset" title="重置视图">复</button></div>
      <div id="sf-minimap" class="sf-minimap" aria-label="Minimap"></div>
      <div class="sf-canvas-hint">${state.editMode ? '规划编辑：端口拖拽只创建 PLANNED 语义边' : '只读：切换“规划编辑”后才能拖拽端口'} · 拖动画布 · 滚轮缩放 · Shift 框选 · Ctrl/⌘ 多选 · Delete 隐藏 · 0 适配 · R 重置 · Ctrl/⌘+S 保存</div>
    `;
    const world = canvas.querySelector('#sf-world');
    world.innerHTML = renderedNodes().map(renderNode).join('');
    applyTransform();
    renderEdges();
    renderMinimap();
    bindCanvasControls(canvas);
    refreshNodeSelection();
  }

  function renderNode(node) {
    if (isPresentationCluster(node)) {
      const expanded = state.expandedPresentationClusters?.has(node.id);
      const typeSummary = Object.entries(node.memberTypes || {})
        .map(([type, count]) => `${nodeLabel(type)} ${count}`)
        .join(' · ');
      const chapterRange = node.chapterFrom != null
        ? `Ch.${node.chapterFrom}–${node.chapterTo}`
        : 'Unplaced activity';
      return `<article class="sf-node sf-node-cluster ${expanded ? 'is-expanded' : ''}" data-node-id="${attr(node.id)}" style="left:${Number(node.x || 0)}px;top:${Number(node.y || 0)}px" tabindex="0" title="Presentation-only activity aggregate"><div class="sf-node-header"><span class="sf-node-kind">ACTIVITY CLUSTER</span><span class="sf-status-badge sf-presentation-badge">VIEW ONLY</span></div><strong class="sf-node-title">${text(node.title)}</strong><div class="sf-node-summary">${text(node.summary || 'Evidence nodes grouped for readability.')}</div><div class="sf-node-meta"><span class="sf-node-badge">${text(chapterRange)}</span><span class="sf-node-badge">${text(typeSummary || `${node.memberCount} evidence nodes`)}</span></div><div class="sf-cluster-actions"><button type="button" class="btn btn-sm btn-secondary" data-sf-cluster-toggle="${attr(node.id)}">${expanded ? 'Collapse group' : `Expand ${text(node.memberCount)} nodes`}</button></div></article>`;
    }
    // Story Ports are semantic affordances, not decoration.  Do not truncate
    // the schema here: Chapter's relationship_changes and foreshadow_out (and
    // the corresponding inputs on other node types) must remain visible and
    // draggable so the author can discover the legal workflow surface.
    const inputs = (node.ports?.inputs || []);
    const outputs = (node.ports?.outputs || []);
    const portClass = state.editMode ? '' : ' is-readonly';
    const portTitle = state.editMode ? '拖到另一个节点的 INPUT 端口以选择语义连接' : '只读模式：切换到规划编辑后才能创建 PLANNED 连接';
    const metadata = node.metadata || {};
    const meta = [
      metadata.number != null ? `Ch.${metadata.number}` : '',
      metadata.number == null && metadata.narrativeOrder != null ? `Narrative ${metadata.narrativeOrder}` : '',
      metadata.storyTime || metadata.event_time || '',
      node.type === 'World' ? 'Hierarchical graph' : '',
      metadata.hierarchyLevelLabel ? `Level · ${metadata.hierarchyLevelLabel}` : '',
      (metadata.currentControlLabel || metadata.currentControl) ? `Control · ${metadata.currentControlLabel || metadata.currentControl}` : '',
      metadata.lifecycleStatus || '',
    ].filter(Boolean).slice(0, 3);
    return `<article class="sf-node ${node.collapsed ? 'is-collapsed' : ''} ${node.hidden ? 'is-hidden' : ''}" data-node-id="${attr(node.id)}" style="left:${Number(node.x || 0)}px;top:${Number(node.y || 0)}px" tabindex="0" title="${attr(`${nodeLabel(node.type)} · ${node.title}`)}">
      <div class="sf-node-header"><span class="sf-node-kind">${text(nodeLabel(node.type))}</span><span class="sf-status-badge ${statusClass(node.status)}">${text(statusLabel(node.status))}</span></div>
      <strong class="sf-node-title">${text(node.title)}</strong>
      <div class="sf-node-summary">${text(node.summary || '暂无摘要')}</div>
      <div class="sf-node-meta">${meta.map((item) => `<span class="sf-node-badge">${text(item)}</span>`).join('')}</div>
      <div class="sf-node-ports"><div class="sf-port-column"><span class="sf-port-label">INPUT</span>${inputs.map((port) => `<button type="button" class="sf-port sf-port-handle is-input${portClass}" data-port-direction="input" data-port-name="${attr(port)}" aria-disabled="${state.editMode ? 'false' : 'true'}" title="${attr(portTitle)}"><span>${text(port)}</span></button>`).join('') || '<span class="sf-port is-empty">—</span>'}</div><div class="sf-port-column output"><span class="sf-port-label">OUTPUT</span>${outputs.map((port) => `<button type="button" class="sf-port sf-port-handle is-output${portClass}" data-port-direction="output" data-port-name="${attr(port)}" aria-disabled="${state.editMode ? 'false' : 'true'}" title="${attr(portTitle)}"><span>${text(port)}</span></button>`).join('') || '<span class="sf-port is-empty">—</span>'}</div></div>
    </article>`;
  }

  function renderEdges() {
    const group = document.getElementById('sf-edge-group');
    if (!group || !state.graph) return;
    const records = renderedEdgeRecords();
    const canvas = document.getElementById('sf-canvas');
    const edgeLayer = document.getElementById('sf-edge-layer');
    const mode = edgeRendererMode(records);
    if (canvas) {
      canvas.dataset.edgeRenderer = mode;
      canvas.dataset.renderedEdges = String(records.length);
      canvas.classList.toggle('is-edge-canvas', mode === 'canvas-2d');
    }
    edgeLayer?.classList.toggle('is-dense', mode === 'canvas-2d');
    if (mode === 'canvas-2d') {
      group.innerHTML = '';
      resizeEdgeCanvas();
      drawDenseEdges(records);
      return;
    }
    clearEdgeCanvas();
    const nodes = new Map(canvasNodes().map((node) => [node.id, node]));
    const edges = records.map((item) => item.edge);
    group.innerHTML = edges.map((edge) => {
      const source = nodes.get(edge.source);
      const target = nodes.get(edge.target);
      const sourcePort = edge.sourcePort || edge.source_port || '';
      const targetPort = edge.targetPort || edge.target_port || '';
      const sourceAnchor = edgeAnchor(source, 'output', sourcePort);
      const targetAnchor = edgeAnchor(target, 'input', targetPort);
      const x1 = sourceAnchor.x;
      const y1 = sourceAnchor.y;
      const x2 = targetAnchor.x;
      const y2 = targetAnchor.y;
      const d = connectionPath(sourceAnchor, targetAnchor);
      const midX = (x1 + x2) / 2;
      const midY = (y1 + y2) / 2 - 4;
      const selected = state.selected.has(edge.source) || state.selected.has(edge.target) || state.edgeSelectedId === edge.id;
      const edgeStatus = String(edge.status || 'CANON').toLowerCase();
      const presentationClass = edge.presentationOnly ? ' is-presentation' : '';
      return `<g class="sf-edge" data-edge-id="${attr(edge.id)}" data-source-port="${attr(sourcePort)}" data-target-port="${attr(targetPort)}"><path class="sf-edge-path ${selected ? 'is-selected' : ''} is-${edgeStatus}${presentationClass}" d="${attr(d)}"></path><text class="sf-edge-label ${selected ? '' : 'is-muted'}${edge.presentationOnly ? ' is-presentation' : ''}" x="${midX}" y="${midY}">${text(edge.label || edge.type)}</text></g>`;
    }).join('');
    group.querySelectorAll('.sf-edge').forEach((element) => {
      const edge = canvasEdges().find((item) => item.id === element.dataset.edgeId);
      if (!edge) return;
      element.addEventListener('click', (event) => {
        event.stopPropagation();
        state.edgeSelectedId = edge.id;
        state.selected = new Set();
        renderEdges();
        renderInspector();
      });
      element.addEventListener('mouseenter', () => element.classList.add('is-hovered'));
      element.addEventListener('mouseleave', () => element.classList.remove('is-hovered'));
    });
  }

  function resizeEdgeCanvas() {
    const edgeCanvas = document.getElementById('sf-edge-canvas');
    const canvas = document.getElementById('sf-canvas');
    if (!edgeCanvas || !canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(rect.width * dpr));
    const height = Math.max(1, Math.round(rect.height * dpr));
    if (edgeCanvas.width !== width || edgeCanvas.height !== height) {
      edgeCanvas.width = width;
      edgeCanvas.height = height;
    }
    edgeCanvas.style.width = `${Math.max(1, rect.width)}px`;
    edgeCanvas.style.height = `${Math.max(1, rect.height)}px`;
    edgeCanvas.dataset.devicePixelRatio = String(dpr);
    return { edgeCanvas, width: rect.width, height: rect.height, dpr };
  }

  function clearEdgeCanvas() {
    const surface = resizeEdgeCanvas();
    const context = surface?.edgeCanvas?.getContext('2d');
    if (!context || !surface) return;
    context.setTransform(surface.dpr, 0, 0, surface.dpr, 0, 0);
    context.clearRect(0, 0, surface.width, surface.height);
    const canvas = document.getElementById('sf-canvas');
    if (canvas) canvas.dataset.edgePaintedEdges = '0';
  }

  function edgeScreenAnchor(node, direction, portName) {
    const anchor = edgeAnchor(node, direction, portName);
    return {
      x: state.transform.tx + anchor.x * state.transform.scale,
      y: state.transform.ty + anchor.y * state.transform.scale,
    };
  }

  function denseEdgeStyle(edge, selected) {
    const status = String(edge.status || 'CANON').toUpperCase();
    if (selected) return { color: '#f4a261', width: 2.7, dash: [] };
    if (status === 'CONFLICT') return { color: '#f85149', width: 2.1, dash: [] };
    if (status === 'PLANNED') return { color: '#f4b566', width: 1.6, dash: [7, 5] };
    if (status === 'CANDIDATE') return { color: '#9fb2d2', width: 1.5, dash: [3, 5] };
    if (edge.presentationOnly) return { color: 'rgba(233,196,106,.72)', width: 1.35, dash: [2, 4] };
    return { color: 'rgba(255,209,168,.58)', width: 1.45, dash: [] };
  }

  function drawDenseEdges(records = renderedEdgeRecords()) {
    const surface = resizeEdgeCanvas();
    const edgeCanvas = surface?.edgeCanvas;
    if (!edgeCanvas) return;
    const context = edgeCanvas.getContext('2d');
    if (!context) return;
    const dpr = surface.dpr;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, surface.width, surface.height);
    context.lineCap = 'round';
    context.lineJoin = 'round';
    const scale = Math.max(state.transform.scale, 0.01);
    records.forEach(({ edge, source, target }) => {
      const sourceAnchor = edgeScreenAnchor(source, 'output', edge.sourcePort || edge.source_port || '');
      const targetAnchor = edgeScreenAnchor(target, 'input', edge.targetPort || edge.target_port || '');
      const bend = Math.max(42 * scale, Math.abs(targetAnchor.x - sourceAnchor.x) * .42);
      const controlOne = { x: sourceAnchor.x + bend, y: sourceAnchor.y };
      const controlTwo = { x: targetAnchor.x - bend, y: targetAnchor.y };
      const selected = state.selected.has(edge.source) || state.selected.has(edge.target) || state.edgeSelectedId === edge.id;
      const style = denseEdgeStyle(edge, selected);
      context.save();
      context.strokeStyle = style.color;
      context.lineWidth = style.width;
      context.setLineDash(style.dash);
      context.globalAlpha = edge.presentationOnly ? .82 : 1;
      context.beginPath();
      context.moveTo(sourceAnchor.x, sourceAnchor.y);
      context.bezierCurveTo(controlOne.x, controlOne.y, controlTwo.x, controlTwo.y, targetAnchor.x, targetAnchor.y);
      context.stroke();
      context.setLineDash([]);
      const angle = Math.atan2(targetAnchor.y - controlTwo.y, targetAnchor.x - controlTwo.x);
      const arrowSize = Math.max(4, Math.min(8, 6 * scale));
      context.fillStyle = style.color;
      context.beginPath();
      context.moveTo(targetAnchor.x, targetAnchor.y);
      context.lineTo(targetAnchor.x - Math.cos(angle - Math.PI / 6) * arrowSize, targetAnchor.y - Math.sin(angle - Math.PI / 6) * arrowSize);
      context.lineTo(targetAnchor.x - Math.cos(angle + Math.PI / 6) * arrowSize, targetAnchor.y - Math.sin(angle + Math.PI / 6) * arrowSize);
      context.closePath();
      context.fill();
      const showLabel = selected || state.edgeHoveredId === edge.id;
      if (showLabel && edge.label) {
        const mid = cubicPoint(sourceAnchor, controlOne, controlTwo, targetAnchor, .5);
        context.font = '10px system-ui, sans-serif';
        context.textAlign = 'center';
        context.textBaseline = 'middle';
        context.lineWidth = 4;
        context.strokeStyle = '#191714';
        context.strokeText(String(edge.label), mid.x, mid.y - 5);
        context.fillStyle = '#fff4de';
        context.fillText(String(edge.label), mid.x, mid.y - 5);
      }
      context.restore();
    });
    const canvas = document.getElementById('sf-canvas');
    if (canvas) canvas.dataset.edgePaintedEdges = String(records.length);
  }

  function cubicPoint(start, controlOne, controlTwo, end, t) {
    const inverse = 1 - t;
    return {
      x: inverse ** 3 * start.x + 3 * inverse ** 2 * t * controlOne.x + 3 * inverse * t ** 2 * controlTwo.x + t ** 3 * end.x,
      y: inverse ** 3 * start.y + 3 * inverse ** 2 * t * controlOne.y + 3 * inverse * t ** 2 * controlTwo.y + t ** 3 * end.y,
    };
  }

  function denseEdgeHit(clientX, clientY) {
    if (!denseEdgeRendererActive()) return null;
    const canvas = document.getElementById('sf-canvas');
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const point = { x: clientX - rect.left, y: clientY - rect.top };
    let closest = null;
    renderedEdgeRecords().forEach(({ edge, source, target }) => {
      const start = edgeScreenAnchor(source, 'output', edge.sourcePort || edge.source_port || '');
      const end = edgeScreenAnchor(target, 'input', edge.targetPort || edge.target_port || '');
      const bend = Math.max(42 * state.transform.scale, Math.abs(end.x - start.x) * .42);
      const controlOne = { x: start.x + bend, y: start.y };
      const controlTwo = { x: end.x - bend, y: end.y };
      let bestDistance = Infinity;
      for (let index = 0; index <= 20; index += 1) {
        const candidate = cubicPoint(start, controlOne, controlTwo, end, index / 20);
        bestDistance = Math.min(bestDistance, Math.hypot(candidate.x - point.x, candidate.y - point.y));
      }
      if (bestDistance <= 9 && (!closest || bestDistance < closest.distance)) closest = { edge, distance: bestDistance };
    });
    return closest?.edge || null;
  }

  function updateDenseEdgeHover(event) {
    if (!denseEdgeRendererActive() || state.drag || state.pan || state.box || state.connection) return;
    const edge = denseEdgeHit(event.clientX, event.clientY);
    const next = edge?.id || null;
    if (next === state.edgeHoveredId) return;
    state.edgeHoveredId = next;
    const canvas = document.getElementById('sf-canvas');
    if (canvas) canvas.classList.toggle('is-edge-hover', Boolean(next));
    drawDenseEdges(renderedEdgeRecords());
  }

  // Port-aware edges make the Story Graph's semantic direction visible.  A
  // port can be absent when one endpoint is outside the incremental viewport
  // or when a legacy edge has no port metadata; those cases intentionally use
  // the old node-side anchor so historical graphs remain renderable.
  function edgeAnchor(node, direction, portName) {
    const port = node && portName ? portElement(node.id, direction, portName) : null;
    if (port) return elementGraphCenter(port);
    return {
      x: Number(node?.x || 0) + (direction === 'output' ? 104 : -104),
      y: Number(node?.y || 0),
    };
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
      minimap.insertAdjacentHTML('beforeend', `<span class="sf-minimap-viewport" data-sf-minimap-viewport="1" title="拖动视口浏览画布" style="left:${viewX}px;top:${viewY}px;width:${viewWidth}px;height:${viewHeight}px"></span>`);
    }
  }

  function bindMinimapControls() {
    const minimap = document.getElementById('sf-minimap');
    if (!minimap || minimap.dataset.sfControlsBound === '1') return;
    minimap.dataset.sfControlsBound = '1';
    const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
    const worldPoint = (event, rect, bounds) => {
      const mapWidth = Math.max(1, rect.width - 10);
      const mapHeight = Math.max(1, rect.height - 10);
      const scale = Math.min(mapWidth / Math.max(bounds.width, 1), mapHeight / Math.max(bounds.height, 1));
      const localX = clamp(event.clientX - rect.left, 5, rect.width - 5);
      const localY = clamp(event.clientY - rect.top, 5, rect.height - 5);
      return {
        x: bounds.minX + (localX - 5) / Math.max(scale, 0.0001),
        y: bounds.minY + (localY - 5) / Math.max(scale, 0.0001),
      };
    };
    const finishMinimapDrag = (event) => {
      if (!state?.minimapDrag || (event?.pointerId != null && event.pointerId !== state.minimapDrag.pointerId)) return;
      try { minimap.releasePointerCapture(state.minimapDrag.pointerId); } catch (_) { /* pointer may already be released */ }
      state.minimapDrag = null;
      minimap.classList.remove('is-dragging');
      minimap.querySelector('.sf-minimap-viewport')?.classList.remove('is-dragging');
      if (!state.pan) scheduleViewportFetch();
    };
    minimap.addEventListener('pointermove', (event) => {
      const drag = state?.minimapDrag;
      if (!drag || event.pointerId !== drag.pointerId) return;
      event.preventDefault();
      const canvas = document.getElementById('sf-canvas');
      if (!canvas) return;
      const point = worldPoint(event, minimap.getBoundingClientRect(), graphBounds(visibleNodes()));
      const canvasRect = canvas.getBoundingClientRect();
      const centerX = point.x - drag.offsetX;
      const centerY = point.y - drag.offsetY;
      state.transform.tx = canvasRect.width / 2 - centerX * state.transform.scale;
      state.transform.ty = canvasRect.height / 2 - centerY * state.transform.scale;
      applyTransform();
    });
    minimap.addEventListener('pointerup', finishMinimapDrag);
    minimap.addEventListener('pointercancel', finishMinimapDrag);
    minimap.addEventListener('pointerdown', (event) => {
      if (event.button !== 0 || !state?.graph?.nodes?.length) return;
      event.preventDefault();
      event.stopPropagation();
      const canvas = document.getElementById('sf-canvas');
      if (!canvas) return;
      const rect = minimap.getBoundingClientRect();
      const bounds = graphBounds(visibleNodes());
      const world = worldPoint(event, rect, bounds);
      if (event.target.closest('.sf-minimap-viewport')) {
        const canvasRect = canvas.getBoundingClientRect();
        const currentCenter = {
          x: (canvasRect.width / 2 - state.transform.tx) / state.transform.scale,
          y: (canvasRect.height / 2 - state.transform.ty) / state.transform.scale,
        };
        state.minimapDrag = {
          pointerId: event.pointerId,
          offsetX: world.x - currentCenter.x,
          offsetY: world.y - currentCenter.y,
        };
        minimap.classList.add('is-dragging');
        event.target.closest('.sf-minimap-viewport')?.classList.add('is-dragging');
        try { minimap.setPointerCapture(event.pointerId); } catch (_) { /* capture is optional */ }
        return;
      }
      const canvasRect = canvas.getBoundingClientRect();
      state.transform.tx = canvasRect.width / 2 - world.x * state.transform.scale;
      state.transform.ty = canvasRect.height / 2 - world.y * state.transform.scale;
      canvas.focus({ preventScroll: true });
      applyTransform();
    });
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
    updateRenderedViewport();
    if (denseEdgeRendererActive()) drawDenseEdges(renderedEdgeRecords());
    renderMinimap();
    // A pan emits many transform updates. Wait for pointerup so a long drag
    // cannot fan out one expensive SQLite projection request per move event.
    if (!state.pan && !state.minimapDrag) scheduleViewportFetch();
  }

  function updateRenderedViewport() {
    const world = document.getElementById('sf-world');
    if (!world || !state?.graph) return;
    const desired = renderedNodes();
    const canvas = document.getElementById('sf-canvas');
    if (canvas) {
      canvas.dataset.viewportCulling = 'enabled';
      canvas.dataset.graphNodes = String(visibleNodes().length);
      canvas.dataset.loadedGraphNodes = String(realNodes().length);
      canvas.dataset.loadedGraphEdges = String(state.graph.edges?.length || 0);
      canvas.dataset.renderedNodes = String(desired.length);
    }
    const current = [...world.querySelectorAll('.sf-node')].map((element) => element.dataset.nodeId);
    const next = desired.map((node) => node.id);
    if (current.length === next.length && current.every((id, index) => id === next[index])) return;
    world.innerHTML = desired.map(renderNode).join('');
    bindNodeInteractions(world);
    renderEdges();
    refreshNodeSelection();
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
    // Large chapter numbers can produce a tall layered layout.  A fixed
    // 28% floor leaves those nodes outside the viewport after fitting; keep
    // the floor low enough for the bounded subgraph, while manual zoom still
    // has its own readable minimum.
    state.transform.scale = Math.max(.08, Math.min(1.08, Math.min(rect.width / bounds.width, rect.height / bounds.height)));
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
    bindMinimapControls();
    if (canvas.dataset.sfControlsBound === '1') {
      bindNodeInteractions(canvas);
      return;
    }
    canvas.dataset.sfControlsBound = '1';
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
    bindNodeInteractions(canvas);
  }

  function bindNodeInteractions(container) {
    const canvas = document.getElementById('sf-canvas');
    container.querySelectorAll('.sf-port-handle').forEach((port) => {
      if (port.dataset.sfBound === '1') return;
      port.dataset.sfBound = '1';
      port.addEventListener('pointerdown', onPortPointerDown);
    });
    container.querySelectorAll('.sf-node').forEach((nodeElement) => {
      if (nodeElement.dataset.sfBound === '1') return;
      nodeElement.dataset.sfBound = '1';
      const displayNode = nodeById(nodeElement.dataset.nodeId);
      if (isPresentationCluster(displayNode)) {
        nodeElement.querySelector('[data-sf-cluster-toggle]')?.addEventListener('click', (event) => {
          event.stopPropagation();
          togglePresentationCluster(displayNode.id);
        });
        nodeElement.addEventListener('pointerdown', (event) => {
          if (event.button !== 0 || event.target.closest('button')) return;
          event.stopPropagation();
          setSelected([displayNode.id], event.ctrlKey || event.metaKey || event.shiftKey);
        });
        nodeElement.addEventListener('dblclick', (event) => {
          event.stopPropagation();
          state.selected = new Set([displayNode.id]);
          renderInspector();
        });
        return;
      }
      nodeElement.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        event.stopPropagation();
        canvas?.focus({ preventScroll: true });
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
        canvas?.setPointerCapture(event.pointerId);
        canvas?.classList.add('is-dragging');
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
    const canvas = document.getElementById('sf-canvas');
    canvas?.focus({ preventScroll: true });
    const edge = denseEdgeHit(event.clientX, event.clientY);
    if (edge) {
      event.preventDefault();
      event.stopPropagation();
      state.edgeSelectedId = edge.id;
      state.edgeHoveredId = edge.id;
      state.selected = new Set();
      state.detail = null;
      renderEdges();
      renderInspector();
      return;
    }
    const rect = canvas.getBoundingClientRect();
    if (event.shiftKey) {
      state.box = { startX: event.clientX - rect.left, startY: event.clientY - rect.top, x: event.clientX - rect.left, y: event.clientY - rect.top };
      renderSelectionBox();
    } else {
      state.pan = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, tx: state.transform.tx, ty: state.transform.ty };
      document.getElementById('sf-canvas').classList.add('is-panning');
    }
    canvas.setPointerCapture(event.pointerId);
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
      return;
    }
    updateDenseEdgeHover(event);
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
      scheduleViewportFetch();
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
    state.edgeHoveredId = null;
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
    document.getElementById('sf-canvas')?.classList.add('is-connecting');
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
    if (!requirePlanningEditMode()) return;
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
      if (!requirePlanningEditMode()) {
        hideEdgeChooser();
        return;
      }
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
    if (isPresentationCluster(node)) {
      menu.innerHTML = `<button data-menu-action="cluster-toggle">${state.expandedPresentationClusters?.has(node.id) ? 'Collapse activity group' : 'Expand activity group'}</button><button data-menu-action="inspect">Open Inspector</button>`;
      document.body.appendChild(menu);
      menu.querySelector('[data-menu-action="cluster-toggle"]')?.addEventListener('click', () => {
        togglePresentationCluster(node.id);
        hideContextMenu();
      });
      menu.querySelector('[data-menu-action="inspect"]')?.addEventListener('click', () => {
        state.selected = new Set([node.id]);
        renderInspector();
        hideContextMenu();
      });
      return;
    }
    menu.innerHTML = `<button data-menu-action="focus">聚焦此节点</button><button data-menu-action="expand">展开下一层</button><button data-menu-action="collapse">${node.collapsed ? '展开节点' : '折叠节点'}</button><button data-menu-action="pin">${node.pinned ? '取消固定位置' : '固定位置'}</button><button data-menu-action="hide">隐藏节点（保存后生效）</button><button data-menu-action="inspect">打开 Inspector</button>`;
    document.body.appendChild(menu);
    menu.querySelectorAll('[data-menu-action]').forEach((button) => button.addEventListener('click', () => {
      const action = button.dataset.menuAction;
      if (action === 'focus') { state.focus = node.id; state.depth = 1; state.selected = new Set([node.id]); loadGraph(); }
      if (action === 'expand') { state.focus = node.id; state.depth = Math.min(3, state.depth + 1); state.selected = new Set([node.id]); loadGraph(); }
      if (action === 'collapse') { node.collapsed = !node.collapsed; renderCanvas(); }
      if (action === 'pin') { node.pinned = !node.pinned; state.layoutDirty = true; renderCanvas(); }
      if (action === 'hide') hideWorkspaceNodes([node]);
      if (action === 'inspect') { state.selected = new Set([node.id]); refreshNodeSelection(); renderInspector(); loadNodeDetail(node.id); }
      hideContextMenu();
    }));
  }

  function hideContextMenu() { document.getElementById('sf-context-menu')?.remove(); hideEdgeChooser(); }

  function onCanvasKeyDown(event) {
    if (event.target?.closest?.('input, textarea, select, button, [contenteditable="true"]')) return;
    const modifier = event.ctrlKey || event.metaKey;
    const key = event.key.toLowerCase();
    if (modifier && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      if (event.shiftKey) redoLayout();
      else undoLayout();
      return;
    }
    if (modifier && key === 's') {
      event.preventDefault();
      saveLayout();
      return;
    }
    if (modifier && key === 'f') {
      event.preventDefault();
      document.getElementById('sf-search')?.focus({ preventScroll: true });
      return;
    }
    if (modifier && key === 'a') {
      event.preventDefault();
      state.selected = new Set(visibleNodes().map((node) => node.id));
      refreshNodeSelection();
      renderInspector();
      return;
    }
    if (modifier && event.key.toLowerCase() === 'y') {
      event.preventDefault();
      redoLayout();
      return;
    }
    if (event.key === 'Delete' || event.key === 'Backspace') {
      hideWorkspaceNodes(selectedNodes());
      event.preventDefault();
    }
    if (event.key === 'Escape') {
      hideContextMenu();
      if (state.connection) stopPortDrag();
      state.selected = new Set();
      refreshNodeSelection();
      renderInspector();
      event.preventDefault();
      return;
    }
    if (event.key === '0' || event.key === 'Home' || key === 'f') { fitGraph(); event.preventDefault(); }
    if (key === 'r') { state.transform = { tx: 0, ty: 0, scale: 1 }; applyTransform(); event.preventDefault(); }
    if (event.key === '1' || event.key === '2' || event.key === '3') {
      state.depth = Number(event.key);
      loadGraph();
      event.preventDefault();
    }
    if (event.key === '+' || event.key === '=') { zoomAt(window.innerWidth / 2, window.innerHeight / 2, 1.15); event.preventDefault(); }
    if (event.key === '-') { zoomAt(window.innerWidth / 2, window.innerHeight / 2, .87); event.preventDefault(); }
  }

  function renderEdgeInspector(edge) {
    const inspector = document.getElementById('sf-inspector');
    if (!inspector || !edge) return;
    const source = nodeById(edge.source);
    const target = nodeById(edge.target);
    if (edge.presentationOnly) {
      inspector.innerHTML = planningEditBanner() + `<div class="sf-inspector-head"><div><h3>Activity evidence</h3><p>Presentation-only grouping</p></div><span class="sf-status-badge sf-presentation-badge">VIEW ONLY</span></div><div class="sf-context-banner sf-presentation-boundary">This line summarizes one or more real semantic edges so a collapsed activity group stays connected. It is not persisted as a Story Graph edge.</div><div class="sf-inspector-section"><h4>Source evidence</h4><dl class="sf-kv"><dt>From</dt><dd>${text(source?.title || edge.source)}</dd><dt>To</dt><dd>${text(target?.title || edge.target)}</dd><dt>Underlying edges</dt><dd>${text(edge.metadata?.edgeCount || 0)}</dd><dt>Semantic types</dt><dd>${text(Object.entries(edge.metadata?.edgeTypes || {}).map(([type, count]) => `${type} ${count}`).join(' · ') || 'not recorded')}</dd><dt>Boundary</dt><dd>sqlite.story_graph_projection</dd></dl></div><div class="sf-inspector-actions"><button class="btn btn-sm btn-secondary" data-sf-edge-focus="source">Focus source</button><button class="btn btn-sm btn-secondary" data-sf-edge-focus="target">Focus target</button><button class="btn btn-sm btn-ghost" data-sf-edge-clear="1">Back to nodes</button></div>`;
      inspector.querySelectorAll('[data-sf-edge-focus]').forEach((button) => button.addEventListener('click', () => {
        const targetId = button.dataset.sfEdgeFocus === 'source' ? edge.source : edge.target;
        const node = nodeById(targetId);
        state.edgeSelectedId = null;
        if (isPresentationCluster(node)) {
          state.selected = new Set([targetId]);
          renderInspector();
          return;
        }
        state.focus = targetId;
        state.selected = new Set([targetId]);
        loadGraph();
      }));
      inspector.querySelector('[data-sf-edge-clear]')?.addEventListener('click', () => {
        state.edgeSelectedId = null;
        renderInspector();
      });
      return;
    }
    const provenance = edge.provenance || [];
    inspector.innerHTML = planningEditBanner() + `<div class="sf-inspector-head"><div><h3>${text(edge.label || edge.type)}</h3><p>Semantic Edge · ${text(edge.type)}</p></div><span class="sf-status-badge ${statusClass(edge.status)}">${text(statusLabel(edge.status))}</span></div><div class="sf-context-banner">这条连线表达的是可查询的故事语义，不是无类型的 related_to。</div><div class="sf-inspector-section"><h4>连接</h4><dl class="sf-kv"><dt>Source</dt><dd>${text(source?.title || edge.source)}</dd><dt>Target</dt><dd>${text(target?.title || edge.target)}</dd><dt>语义</dt><dd>${text(edge.type)}</dd><dt>权重</dt><dd>${text(edge.weight ?? '—')}</dd><dt>置信度</dt><dd>${text(edge.confidence ?? '—')}</dd>${edge.first_chapter != null ? `<dt>起始章节</dt><dd>${text(edge.first_chapter)}</dd>` : ''}${edge.last_chapter != null ? `<dt>最近章节</dt><dd>${text(edge.last_chapter)}</dd>` : ''}</dl></div><div class="sf-inspector-section"><h4>Provenance</h4>${provenance.length ? `<div class="sf-provenance">${provenance.slice(0, 8).map((item) => `<div>· ${text(item.kind || 'source')} ${item.table ? `<code>${text(item.table)}</code>` : ''} ${item.id ? `<code>${text(item.id)}</code>` : ''}</div>`).join('')}</div>` : '<p class="sf-provenance">未记录可展示的来源链。</p>'}</div><div class="sf-inspector-actions"><button class="btn btn-sm btn-secondary" data-sf-edge-focus="source">聚焦 Source</button><button class="btn btn-sm btn-secondary" data-sf-edge-focus="target">聚焦 Target</button><button class="btn btn-sm btn-ghost" data-sf-edge-clear="1">返回节点</button></div>`;
    inspector.querySelectorAll('[data-sf-edge-focus]').forEach((button) => button.addEventListener('click', () => {
      const targetId = button.dataset.sfEdgeFocus === 'source' ? edge.source : edge.target;
      state.edgeSelectedId = null;
      state.focus = targetId;
      state.selected = new Set([targetId]);
      loadGraph();
    }));
    inspector.querySelector('[data-sf-edge-clear]')?.addEventListener('click', () => {
      state.edgeSelectedId = null;
      renderInspector();
    });
  }

  function renderGenerationContextGraphSection(graphState, title = 'AI Context Graph') {
    if (!graphState) return '';
    if (graphState.loading) {
      return `<div class="sf-inspector-section sf-context-graph-evidence"><h4>${text(title)}</h4><p class="dim-note">Reading the persisted Context Graph from SQLite GenerationRun…</p></div>`;
    }
    if (graphState.error) {
      return `<div class="sf-inspector-section sf-context-graph-evidence"><h4>${text(title)}</h4><div class="sf-context-banner sf-context-excluded">Context Graph read failed: ${text(graphState.error)}</div><button class="btn btn-sm btn-ghost" data-sf-context-graph-load="${attr(graphState.runId || '')}">Retry</button></div>`;
    }
    const snapshot = graphState.snapshot || {};
    if (!graphState.available || !snapshot.available) {
      return `<div class="sf-inspector-section sf-context-graph-evidence"><h4>${text(title)}</h4><div class="sf-context-banner sf-context-excluded">Context Graph unavailable${graphState.reason ? ` · ${text(graphState.reason)}` : ''}</div><p class="dim-note">This run has no captured metadata-only graph. The Canvas will not infer AI context from the current Story Graph or prompt text.</p></div>`;
    }
    const nodes = Array.isArray(snapshot.nodes) ? snapshot.nodes : [];
    const edges = Array.isArray(snapshot.edges) ? snapshot.edges : [];
    const nodeByContextId = new Map(nodes.map((node) => [String(node.id || ''), node]));
    const titleFor = (nodeId) => nodeByContextId.get(String(nodeId || ''))?.title || nodeId || '—';
    const nodeRows = nodes.slice(0, 32).map((node) => {
      const included = node.included !== false;
      const reason = included ? (node.inclusionReason || node.reason || 'included') : (node.excludedReason || node.reason || 'excluded');
      const explainability = node.explainability || {};
      const focus = explainability.focusNodeId ? ` · focus ${explainability.focusNodeId}` : '';
      const role = explainability.selectionRole ? ` · ${explainability.selectionRole}` : '';
      return `<li><span><b>${text(node.type || 'ContextSource')}</b> · ${text(node.title || node.id || '—')}<small>${text(node.id || '')} · ${text(reason)}${role}${focus}</small></span><span class="sf-neighbor-edge">${included ? 'INCLUDED' : 'EXCLUDED'}</span></li>`;
    }).join('');
    const edgeRows = edges.slice(0, 48).map((edge) => `<li><b>${text(edge.type || 'semantic')}</b><span>${text(titleFor(edge.source))} → ${text(titleFor(edge.target))}</span><small>${text(edge.label || edge.reason || '')}</small></li>`).join('');
    const includedEdges = edges.filter((edge) => edge.included !== false).length;
    const excludedEdges = edges.filter((edge) => edge.included === false).length;
    return `<div class="sf-inspector-section sf-context-graph-evidence"><h4>${text(title)}</h4><div class="sf-context-banner ${snapshot.valid ? '' : 'sf-context-excluded'}"><b>${snapshot.valid ? 'Integrity verified' : 'Integrity invalid'}</b> · metadata-only read model<br><small>${text(snapshot.nodeCount ?? nodes.length)} nodes · ${text(snapshot.edgeCount ?? edges.length)} edges · ${text(includedEdges)} included / ${text(excludedEdges)} excluded · focus ${text((snapshot.focusNodeIds || []).join(', ') || 'none')}</small><br><small>graph hash ${text(snapshot.graphSha256 || 'unavailable')} · ${text(snapshot.integrityReason || 'hash status recorded by SQLite projector')}</small></div><details open><summary>Context sources (${text(nodes.length)}${snapshot.truncated ? '+' : ''})</summary><ul class="sf-inspector-list">${nodeRows || '<li>No source nodes recorded.</li>'}</ul></details><details><summary>Context edges (${text(edges.length)}${snapshot.truncated ? '+' : ''})</summary><ul class="sf-inspector-list sf-context-graph-edge-list">${edgeRows || '<li>No context edges recorded.</li>'}</ul></details><p class="dim-note">Source labels, inclusion decisions, and semantic selection evidence are persisted in the GenerationRun manifest. Prompt prose and provider credentials are not exposed here.</p><button class="btn btn-sm btn-ghost" data-sf-context-graph-load="${attr(graphState.runId || '')}">Refresh Context Graph</button></div>`;
  }

  function renderCandidateComparison(inspector) {
    const comparison = state.candidateComparison || {};
    if (comparison.loading) {
      inspector.innerHTML = `${planningEditBanner()}<div class="sf-inspector-empty"><div><strong>正在读取候选方案比较</strong><br><span>从 SQLite planning overlay 计算步骤与语义边差异。</span></div></div>`;
      return;
    }
    if (comparison.error) {
      inspector.innerHTML = `${planningEditBanner()}<div class="sf-inspector-empty"><div><strong>候选方案比较失败</strong><br><span>${text(comparison.error)}</span><br><button class="btn btn-sm btn-ghost" data-sf-comparison-close>返回节点</button></div></div>`;
      inspector.querySelector('[data-sf-comparison-close]')?.addEventListener('click', () => {
        state.candidateComparison = null;
        renderInspector();
      });
      return;
    }
    const candidateSet = comparison.candidateSet || {};
    const branches = Array.isArray(comparison.branches) ? comparison.branches : [];
    const pairwise = Array.isArray(comparison.pairwise) ? comparison.pairwise : [];
    const renderSemanticEdge = (edge) => `<li><b>${text(edge.type || 'semantic')}</b><span>${text(edge.source || '—')} → ${text(edge.target || '—')}</span><small>${text(edge.label || '')}</small></li>`;
    const renderSteps = (steps) => (Array.isArray(steps) && steps.length
      ? `<ol class="sf-comparison-steps">${steps.slice(0, 16).map((step) => `<li>${text(step)}</li>`).join('')}</ol>`
      : '<p class="dim-note">没有结构化步骤。</p>');
    const renderBranch = (branch) => `<article class="sf-comparison-branch">
      <div class="sf-comparison-branch-head"><div><span class="sf-candidate-branch-index">${text(branch.branchIndex ?? '—')}.</span><b>${text(branch.title || '未命名方案')}</b><p>${text(branch.summary || '没有摘要')}</p></div><span class="sf-status-badge ${statusClass(branch.status)}">${text(branch.status || 'CANDIDATE')}</span></div>
      <dl class="sf-kv"><dt>评分</dt><dd>${text(branch.score ?? '—')}</dd><dt>风险</dt><dd>${text((branch.risks || []).join('、') || '未记录')}</dd><dt>步骤</dt><dd>${text((branch.steps || []).length)}</dd><dt>语义边</dt><dd>${text((branch.semanticEdges || []).length)}</dd></dl>
      <div class="sf-inspector-section"><h5>方案步骤</h5>${renderSteps(branch.steps)}</div>
      ${(branch.semanticEdges || []).length ? `<div class="sf-inspector-section"><h5>方案语义边</h5><ul class="sf-comparison-edge-list">${branch.semanticEdges.slice(0, 12).map(renderSemanticEdge).join('')}</ul></div>` : ''}
      <button class="btn btn-sm btn-secondary" data-sf-comparison-focus="${attr(branch.candidateBranchId)}">在 StoryFlow 中定位</button>
    </article>`;
    const renderDelta = (delta) => `<article class="sf-comparison-delta"><div class="sf-comparison-delta-head"><b>${text(delta.branchId)}</b><span>相对基准 ${text(delta.baselineBranchId)}</span></div><dl class="sf-kv"><dt>共同步骤</dt><dd>${text(delta.sharedStepCount ?? 0)}</dd></dl><div class="sf-comparison-delta-grid"><div><h5>新增步骤</h5>${renderSteps(delta.addedSteps)}</div><div><h5>移除步骤</h5>${renderSteps(delta.removedSteps)}</div></div>${(delta.addedSemanticEdges || []).length ? `<div><h5>新增语义边</h5><ul class="sf-comparison-edge-list">${delta.addedSemanticEdges.map(renderSemanticEdge).join('')}</ul></div>` : ''}${(delta.removedSemanticEdges || []).length ? `<div><h5>移除语义边</h5><ul class="sf-comparison-edge-list">${delta.removedSemanticEdges.map(renderSemanticEdge).join('')}</ul></div>` : ''}</article>`;
    inspector.innerHTML = `${planningEditBanner()}<div class="sf-inspector-head"><div><h3>候选方案比较</h3><p>${text(candidateSet.originTitle || 'StoryFlow 分支集合')}</p></div><span class="sf-status-badge status-candidate">READ ONLY</span></div>
      <div class="sf-context-banner">${text(comparison.planningBoundary || '比较结果来自 planning overlay，不会修改 Canon。')}</div>
      <div class="sf-inspector-section"><h4>比较范围</h4><dl class="sf-kv"><dt>Candidate set</dt><dd>${text(candidateSet.candidateSetId || comparison.candidateSetId || '—')}</dd><dt>方案数</dt><dd>${text(branches.length)}</dd><dt>基准方案</dt><dd>${text(comparison.baselineBranchId || '—')}</dd><dt>来源</dt><dd>${text(comparison.canonicalSource || 'sqlite.plot_workspaces')}</dd></dl></div>
      <div class="sf-inspector-section"><h4>共同结构</h4><p class="dim-note">这些步骤与语义边同时出现在所选方案中。</p>${renderSteps(comparison.commonSteps)}${(comparison.commonSemanticEdges || []).length ? `<ul class="sf-comparison-edge-list">${comparison.commonSemanticEdges.map(renderSemanticEdge).join('')}</ul>` : '<p class="dim-note">没有共同语义边。</p>'}</div>
      <div class="sf-inspector-section"><h4>方案详情</h4><div class="sf-comparison-branches">${branches.map(renderBranch).join('')}</div></div>
      ${pairwise.length ? `<div class="sf-inspector-section"><h4>相对差异</h4><div class="sf-comparison-deltas">${pairwise.map(renderDelta).join('')}</div></div>` : ''}
      <div class="sf-inspector-actions"><button class="btn btn-sm btn-ghost" data-sf-comparison-close>返回节点</button></div>`;
    inspector.querySelectorAll('[data-sf-comparison-focus]').forEach((button) => button.addEventListener('click', () => {
      state.candidateComparison = null;
      focusCandidateBranch(button.dataset.sfComparisonFocus);
    }));
    inspector.querySelector('[data-sf-comparison-close]')?.addEventListener('click', () => {
      state.candidateComparison = null;
      renderInspector();
    });
  }

  function renderCandidateLineage(inspector) {
    const lineage = state.candidateLineage || {};
    if (lineage.loading) {
      inspector.innerHTML = `${planningEditBanner()}<div class="sf-inspector-empty"><div><strong>正在读取候选分支谱系</strong><br><span>从 SQLite planning overlay 解析父分支与后续推演。</span></div></div>`;
      return;
    }
    if (lineage.error) {
      inspector.innerHTML = `${planningEditBanner()}<div class="sf-inspector-empty"><div><strong>候选分支谱系读取失败</strong><br><span>${text(lineage.error)}</span><br><button class="btn btn-sm btn-ghost" data-sf-lineage-close>返回节点</button></div></div>`;
      inspector.querySelector('[data-sf-lineage-close]')?.addEventListener('click', () => {
        state.candidateLineage = null;
        renderInspector();
      });
      return;
    }
    const payload = lineage.lineage || {};
    const nodes = Array.isArray(payload.nodes) ? payload.nodes : [];
    const edges = Array.isArray(payload.edges) ? payload.edges : [];
    const missingParents = Array.isArray(payload.missingParents) ? payload.missingParents : [];
    const parentByChild = new Map(edges.map((edge) => [String(edge.source || ''), edge]));
    const branchByRoot = new Map((state.candidateSets || []).flatMap((candidateSet) => (
      candidateSet.branches || []
    )).map((branch) => [String(branch.rootNodeId || ''), branch]));
    const nodeRows = nodes.map((node) => {
      const branch = branchByRoot.get(String(node.rootNodeId || ''));
      const parentEdge = parentByChild.get(String(node.rootNodeId || ''));
      const relation = parentEdge ? `源自 ${parentEdge.target}` : '根分支';
      return `<li><span><b>${text(node.title || node.rootNodeId)}</b><small>${text(node.candidateSetId)} · ${text(node.candidateBranchId)} · ${text(relation)}</small></span><span class="sf-status-badge ${statusClass(node.status)}">${text(node.status)}</span>${branch ? `<button class="btn btn-sm btn-ghost" data-sf-lineage-focus-branch="${attr(branch.candidateBranchId)}">定位</button>` : ''}</li>`;
    }).join('');
    const missingRows = missingParents.map((item) => `<li><b>${text(item.reason || 'parent unavailable')}</b><small>${text(item.child?.candidateBranchId || 'child')} ← ${text(item.parent?.candidateBranchId || item.parent?.rootNodeId || 'unknown parent')}</small></li>`).join('');
    inspector.innerHTML = `${planningEditBanner()}<div class="sf-inspector-head"><div><h3>候选分支谱系</h3><p>Parent / child reforecast lineage</p></div><span class="sf-status-badge status-candidate">READ ONLY</span></div>
      <div class="sf-context-banner">${text(payload.planningBoundary || 'planning_overlay_only')} · 谱系来自 SQLite plot_workspaces；不会修改 Canon。</div>
      <div class="sf-inspector-section"><h4>谱系范围</h4><dl class="sf-kv"><dt>展开方向</dt><dd>${text(payload.direction || 'both')}</dd><dt>深度</dt><dd>${text(payload.depth ?? '—')}</dd><dt>节点</dt><dd>${text(payload.nodeCount ?? nodes.length)}</dd><dt>谱系边</dt><dd>${text(payload.edgeCount ?? edges.length)}</dd><dt>来源</dt><dd>${text(payload.canonicalSource || 'sqlite.plot_workspaces')}</dd></dl></div>
      <div class="sf-inspector-section"><h4>候选分支</h4><ul class="sf-inspector-list sf-candidate-lineage-list">${nodeRows || '<li>当前范围没有候选分支。</li>'}</ul></div>
      ${missingParents.length ? `<div class="sf-inspector-section"><h4>未解析的父分支</h4><div class="sf-context-banner sf-context-excluded">缺失或错配的父标识被保留为 unavailable；不会猜测连接。</div><ul class="sf-inspector-list">${missingRows}</ul></div>` : ''}
      <div class="sf-inspector-actions"><button class="btn btn-sm btn-ghost" data-sf-lineage-close>返回节点</button></div>`;
    inspector.querySelectorAll('[data-sf-lineage-focus-branch]').forEach((button) => button.addEventListener('click', () => {
      state.candidateLineage = null;
      focusCandidateBranch(button.dataset.sfLineageFocusBranch);
    }));
    inspector.querySelector('[data-sf-lineage-close]')?.addEventListener('click', () => {
      state.candidateLineage = null;
      renderInspector();
    });
  }

  async function loadCandidateLineage({ candidateSetId = '', candidateBranchId = '', rootNodeId = '', depth = 3, direction = 'both' } = {}) {
    if (!state || !S.book) return;
    state.candidateLineage = { loading: true, error: null };
    state.candidateComparison = null;
    renderInspector();
    try {
      const query = new URLSearchParams({ depth: String(depth), direction });
      if (candidateSetId) query.set('candidateSetId', candidateSetId);
      if (candidateBranchId) query.set('candidateBranchId', candidateBranchId);
      if (rootNodeId) query.set('rootNodeId', rootNodeId);
      const payload = await api('GET', `/books/${currentBook()}/story-graph/candidates/lineage?${query.toString()}`);
      if (!state) return;
      state.candidateLineage = { lineage: payload.lineage || {}, loading: false, error: null };
    } catch (error) {
      if (!state) return;
      state.candidateLineage = { loading: false, error: error.message };
    }
    renderInspector();
  }

  function renderPresentationClusterInspector(node) {
    const members = realNodes().filter((item) => (node.memberIds || []).includes(item.id));
    const typeCounts = Object.entries(node.memberTypes || {}).map(([type, count]) => `${nodeLabel(type)} ${count}`).join(' · ');
    const edgeCounts = Object.entries(node.edgeTypeCounts || {}).map(([type, count]) => `${type} ${count}`).join(' · ');
    const rows = members.slice(0, 24).map((member) => `<li><button data-sf-cluster-member="${attr(member.id)}">${text(nodeLabel(member.type))} · ${text(member.title)}</button><span>${text(member.status || 'CANON')}</span></li>`).join('');
    return `<div class="sf-inspector-head"><div><h3>${text(node.title)}</h3><p>Presentation-only aggregate · ${text(node.source || 'sqlite.story_graph_projection')}</p></div><span class="sf-status-badge sf-presentation-badge">VIEW ONLY</span></div><div class="sf-context-banner sf-presentation-boundary"><b>This is a display projection.</b><br>It groups real SQLite Story Graph nodes for progressive disclosure. It is not a StoryFact, StoryState, StoryCommit, or semantic edge.</div><div class="sf-inspector-section"><h4>Evidence range</h4><dl class="sf-kv"><dt>Members</dt><dd>${text(node.memberCount || members.length)} real nodes</dd><dt>Types</dt><dd>${text(typeCounts || 'not recorded')}</dd><dt>Chapters</dt><dd>${text(node.chapterFrom != null ? `Ch.${node.chapterFrom}–${node.chapterTo}` : 'unplaced activity')}</dd><dt>Source edges</dt><dd>${text(edgeCounts || 'none')}</dd></dl></div><div class="sf-inspector-section"><h4>Source members</h4><ul class="sf-inspector-list sf-cluster-member-list">${rows || '<li class="dim-note">No source members in this bounded projection.</li>'}</ul>${members.length > 24 ? `<p class="dim-note">Showing 24 of ${text(members.length)} source nodes; expand the group to inspect the rest.</p>` : ''}</div><div class="sf-inspector-actions"><button class="btn btn-sm btn-secondary" data-sf-cluster-expand>${state.expandedPresentationClusters?.has(node.id) ? 'Collapse group' : 'Expand group'}</button></div>`;
  }

  function renderInspector() {
    const inspector = document.getElementById('sf-inspector');
    if (!inspector) return;
    if (state.candidateLineage) {
      renderCandidateLineage(inspector);
      return;
    }
    if (state.candidateComparison) {
      renderCandidateComparison(inspector);
      return;
    }
    const selectedEdge = state.edgeSelectedId && canvasEdges().find((edge) => edge.id === state.edgeSelectedId);
    if (selectedEdge) {
      renderEdgeInspector(selectedEdge);
      return;
    }
    const nodes = selectedNodes();
    if (!nodes.length) {
      inspector.innerHTML = `<div class="sf-inspector-empty">${planningEditBanner()}<div><strong>选择一个故事节点</strong><br>这里会显示它的状态、来源、语义关系和可执行动作。<br><br>Canvas 上的坐标属于工作区，不会写入 StoryFact。</div></div>`;
      return;
    }
    if (nodes.length > 1) {
      const planningWriteAttrs = state.editMode ? '' : 'disabled aria-disabled="true"';
      const analysisAttrs = analysisNodes().length && modelRuntimeReady() ? '' : 'disabled aria-disabled="true"';
      const selectionState = state.selectionProjection;
      const projection = !selectionState?.loading && !selectionState?.error
        && selectionProjectionMatches(nodes, selectionState)
        ? selectionState
        : null;
      inspector.innerHTML = `<div class="sf-inspector-head"><div><h3>${nodes.length} 个节点</h3><p>多选子图 · StoryFlow working set</p></div><span class="sf-status-badge status-planned">SELECTION</span></div>${planningEditBanner()}<div class="sf-context-banner">这组节点是一个可执行的 StoryFlow 工作单元：其内部语义边会进入章节 Intent、AI 分析或候选推演。摘要来自 SQLite Story Graph；不会把布局或模型猜测当作事实。</div>${renderSelectionProjection(projection, nodes)}<div class="sf-inspector-section"><h4>选中节点</h4><ul class="sf-inspector-list">${nodes.slice(0, 12).map((node) => `<li><span>${text(nodeLabel(node.type))} · ${text(node.title)}</span><small>${text(statusLabel(node.status))}</small></li>`).join('')}</ul>${nodes.length > 12 ? `<p class="dim-note">还有 ${text(nodes.length - 12)} 个节点未在 Inspector 展开。</p>` : ''}</div>`;
      inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-actions"><button class="btn btn-sm btn-secondary" data-sf-selection-action="intent" ${planningWriteAttrs}>保存章节计划</button><button class="btn btn-sm btn-primary" data-sf-selection-action="generate" ${planningWriteAttrs}>生成章节</button><button class="btn btn-sm btn-secondary" data-sf-selection-action="analyze" ${analysisAttrs}>AI 分析选择</button></div>`);
      inspector.querySelector('[data-sf-selection-action="intent"]')?.addEventListener('click', generateIntentFromSelection);
      inspector.querySelector('[data-sf-selection-action="generate"]')?.addEventListener('click', generateChapterFromSelection);
      inspector.querySelector('[data-sf-selection-action="analyze"]')?.addEventListener('click', analyzeSelection);
      const externalPage = projection?.meta?.externalEdgesPage || projection?.externalEdgesPage || {};
      if (externalPage.hasMore && externalPage.nextPageToken) {
        inspector.querySelector('.sf-selection-projection')?.insertAdjacentHTML(
          'beforeend',
          `<button class="btn btn-sm btn-ghost sf-selection-load-more" data-sf-selection-load-more ${projection.externalEdgesLoading ? 'disabled' : ''}>${projection.externalEdgesLoading ? 'Loading…' : `Load more external edges (${text(externalPage.offset + externalPage.limit)} / ${text(externalPage.total)})`}</button>`,
        );
      }
      inspector.querySelector('[data-sf-selection-load-more]')?.addEventListener('click', loadMoreSelectionEdges);
      inspector.querySelectorAll('[data-sf-selection-focus]').forEach((button) => button.addEventListener('click', () => {
        const targetId = button.dataset.sfSelectionFocus;
        const target = nodeById(targetId);
        if (!target) {
          // The endpoint is intentionally outside the current bounded page.
          // Use its recorded type to issue a fresh authoritative focus query
          // instead of silently treating a known relationship as absent.
          state.view = TYPE_VIEW[button.dataset.sfSelectionFocusType] || state.view;
          state.focus = targetId;
          state.selected = new Set([targetId]);
          state.selectionProjection = null;
          loadGraph();
          return;
        }
        state.selected = new Set([target.id]);
        state.focus = target.id;
        state.selectionProjection = null;
        refreshNodeSelection();
        renderInspector();
        loadNodeDetail(target.id);
        centerOn(target);
      }));
      if (!projection && !selectionState?.loading && !selectionState?.error) loadSelectionProjection(nodes);
      if (state.analysisResult) renderAnalysisResult(inspector);
      return;
    }
    const node = nodes[0];
    if (isPresentationCluster(node)) {
      inspector.innerHTML = planningEditBanner() + renderPresentationClusterInspector(node);
      inspector.querySelector('[data-sf-cluster-expand]')?.addEventListener('click', () => togglePresentationCluster(node.id));
      inspector.querySelectorAll('[data-sf-cluster-member]').forEach((button) => button.addEventListener('click', () => {
        const member = realNodes().find((item) => item.id === button.dataset.sfClusterMember);
        if (!member) return;
        const parentCluster = presentationClusters().find((cluster) => (cluster.memberIds || []).includes(member.id));
        if (!state.expandedPresentationClusters) state.expandedPresentationClusters = new Set();
        if (parentCluster && !state.expandedPresentationClusters?.has(parentCluster.id)) {
          state.expandedPresentationClusters.add(parentCluster.id);
          applyPresentationLayout();
          renderCanvas();
          renderSidebar();
        }
        state.selected = new Set([member.id]);
        state.focus = member.id;
        refreshNodeSelection();
        renderInspector();
        loadNodeDetail(member.id);
        centerOn(member);
      }));
      return;
    }
    const detail = state.detail && state.detail.node?.id === node.id ? state.detail : null;
    inspector.innerHTML = planningEditBanner() + renderInspectorNode(node, detail);
    if (node.type === 'ContextSource' || state.contextEvidence?.nodeId === node.id) {
      const metadata = {
        ...(node.metadata || {}),
        ...(state.contextEvidence?.nodeId === node.id ? state.contextEvidence : {}),
      };
      const range = metadata.persistedPromptRange || metadata.promptRange || metadata.contextRange;
      const rangeText = range
        ? `${range.scope || 'context'} · ${text(range.start)}–${text(range.end)} chars · ${text(range.precision || 'recorded')}`
        : `unavailable${metadata.persistedPromptRangeStatus ? ` · ${text(metadata.persistedPromptRangeStatus)}` : ''}`;
      inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section"><h4>Prompt range binding</h4><p class="dim-note">${rangeText}</p><p class="dim-note">这是 GenerationRun 持久化输入的字符级范围；不是 Provider token 偏移。</p></div>`);
    }
    const actionBar = inspector.querySelector('.sf-inspector-actions');
    if (actionBar) actionBar.insertAdjacentHTML('beforeend', `<button class="btn btn-sm btn-secondary" data-sf-inspector-action="history">${node.type === 'Chapter' ? 'StoryCommit / History' : 'History'}</button>`);
    if (actionBar && state.view === 'all' && state.graph?.meta?.viewport?.crossBoundaryEdgesTruncated) {
      const boundaryAction = state.graph.meta.viewport.nextBoundaryPageToken ? 'Load more boundary edges' : 'Load boundary edge page';
      actionBar.insertAdjacentHTML('beforeend', `<button class="btn btn-sm btn-ghost" data-sf-boundary-next="${attr(node.id)}">${boundaryAction}</button>`);
    }
    if (actionBar && detail?.pagination?.hasMore) actionBar.insertAdjacentHTML('beforeend', `<button class="btn btn-sm btn-ghost" data-sf-inspector-action="neighbors">${state.neighborLoading ? '读取中…' : '加载更多邻居'}</button>`);
    if (state.impact && state.impact.nodeId === node.id) renderImpactResult(inspector);
    if (state.chapterImpact && state.chapterImpact.nodeId === node.id) renderChapterEditImpactResult(inspector);
    if (state.history && state.history.nodeId === node.id) renderHistoryResult(inspector);
    if (state.analysisResult) renderAnalysisResult(inspector);
    const nodeMetadata = node.metadata || {};
   inspector.querySelectorAll('[data-sf-neighbor]').forEach((button) => button.addEventListener('click', () => {
     const target = nodeById(button.dataset.sfNeighbor);
     if (target) { state.selected = new Set([target.id]); refreshNodeSelection(); renderInspector(); loadNodeDetail(target.id); centerOn(target); }
   }));
    inspector.querySelectorAll('[data-sf-boundary-node]').forEach((button) => button.addEventListener('click', () => {
      const targetId = button.dataset.sfBoundaryNode;
      if (!targetId) return;
      state.focus = targetId;
      state.depth = 1;
      state.selected = new Set([targetId]);
      state.detail = null;
      loadGraph();
    }));
    inspector.querySelectorAll('[data-sf-boundary-next]').forEach((button) => button.addEventListener('click', () => {
      loadNextBoundaryPage(button.dataset.sfBoundaryNext || node.id);
    }));
    inspector.querySelectorAll('[data-sf-generation-trace]').forEach((button) => button.addEventListener('click', () => {
      loadGenerationRunTrace(button.dataset.sfGenerationTrace || '');
    }));
    inspector.querySelectorAll('[data-sf-reconcile-task]').forEach((button) => button.addEventListener('click', () => {
      reconcilePlanningTask(button.dataset.sfReconcileTask || '');
    }));
    inspector.querySelector('[data-sf-candidate-lineage]')?.addEventListener('click', () => {
      loadCandidateLineage({
        candidateSetId: nodeMetadata.candidateSetId || '',
        candidateBranchId: nodeMetadata.candidateBranchId || '',
        rootNodeId: node.id,
        depth: Math.max(3, state.depth || 1),
        direction: 'both',
      });
    });
    inspector.querySelector('[data-sf-candidate-reforecast]')?.addEventListener('click', () => {
      generateCandidateBranches('', {
        candidateSetId: nodeMetadata.candidateSetId || '',
        candidateBranchId: nodeMetadata.candidateBranchId || '',
        candidateRootNodeId: node.id,
      });
    });
    inspector.querySelectorAll('[data-sf-context-graph-load]').forEach((button) => button.addEventListener('click', () => {
      loadGenerationRunContextGraph(button.dataset.sfContextGraphLoad || '');
    }));
    inspector.querySelectorAll('[data-sf-inspector-action]').forEach((button) => button.addEventListener('click', () => {
      const action = button.dataset.sfInspectorAction;
      if (action === 'focus') { state.focus = node.id; state.depth = 1; loadGraph(); }
      if (action === 'expand') { state.focus = node.id; state.depth = Math.min(3, state.depth + 1); loadGraph(); }
      if (action === 'context' && node.type === 'Chapter') loadContext(node.id);
      if (action === 'timeline' && node.type === 'Character') { state.view = 'timeline'; state.focus = node.id; state.depth = 1; loadGraph(); }
      if (action === 'character-analyze' && node.type === 'Character') { state.selected = new Set([node.id]); refreshNodeSelection(); renderInspector(); analyzeSelection(); }
      if (action === 'impact') loadImpact(node.id);
      if (action === 'chapter-impact' && node.type === 'Chapter') loadChapterEditImpact(node.id);
      if (action === 'history') loadHistory(node.id);
      if (action === 'neighbors') loadMoreNeighbors(node.id);
      if (action === 'audit' && node.type === 'Chapter') window.openChapterStudioAction?.('audit', node.metadata?.number);
      if (action === 'rewrite' && node.type === 'Chapter') window.openChapterStudioAction?.('rewrite', node.metadata?.number);
      if (action === 'versions' && node.type === 'Chapter') window.openChapterStudioAction?.('versions', node.metadata?.number);
      if (action === 'open') openNodeAction(node);
    }));
    inspector.querySelector('[data-sf-context-chapter]')?.addEventListener('click', () => {
      state.focus = inspector.querySelector('[data-sf-context-chapter]').dataset.sfContextChapter;
      state.selected = new Set([state.focus]);
      renderCanvas();
      renderSidebar();
      renderInspector();
      const chapter = nodeById(state.focus);
      if (chapter) centerOn(chapter);
    });
    inspector.querySelector('[data-sf-context-back]')?.addEventListener('click', () => renderContextInspector());
  }

  function selectionProjectionMatches(nodes, projection) {
    const ids = [...new Set(nodes.map((node) => node.id))].sort();
    const projectedIds = [...new Set(projection?.nodeIds || [])].sort();
    return projectedIds.length === ids.length
      && projectedIds.every((id, index) => id === ids[index]);
  }

  function renderSelectionProjection(projection, nodes) {
    if (state.selectionProjection?.loading && !projection) {
      return '<div class="sf-inspector-section sf-selection-projection"><h4>语义流摘要</h4><p class="dim-note">正在读取选区内的 SQLite 语义边…</p></div>';
    }
    if (state.selectionProjection?.error && !projection) {
      return `<div class="sf-inspector-section sf-selection-projection"><h4>语义流摘要</h4><div class="sf-context-banner sf-context-excluded">选区摘要读取失败：${text(state.selectionProjection.error)}</div></div>`;
    }
    if (!projection) {
      return '<div class="sf-inspector-section sf-selection-projection"><h4>语义流摘要</h4><p class="dim-note">等待 SQLite 选区投影…</p></div>';
    }
    const summary = projection.summary || {};
    const typeCounts = Object.entries(summary.nodeTypeCounts || {}).map(([type, count]) => `${nodeLabel(type)} ${count}`).join(' · ');
    const statusCounts = Object.entries(summary.nodeStatusCounts || {}).map(([status, count]) => `${statusLabel(status)} ${count}`).join(' · ');
    const edgeCounts = Object.entries(summary.edgeTypeCounts || {}).map(([type, count]) => `${type} ${count}`).join(' · ');
    const externalCounts = Object.entries(summary.externalEdgeTypeCounts || {}).map(([type, count]) => `${type} ${count}`).join(' · ');
    const internalEdges = Array.isArray(projection.internalEdges) ? projection.internalEdges : [];
    const externalEdges = Array.isArray(projection.externalEdges) ? projection.externalEdges : [];
    const edgeRow = (edge, external = false) => {
      const source = nodeById(edge.source) || edge.remoteEndpoint;
      const target = nodeById(edge.target) || edge.remoteEndpoint;
      const sourceTitle = source?.title || edge.source;
      const targetTitle = target?.title || edge.target;
      const focusId = external ? edge.remoteEndpointId : '';
      const focusType = external ? (edge.remoteEndpoint?.type || '') : '';
      return `<li class="sf-selection-edge-row">${focusId ? `<button data-sf-selection-focus="${attr(focusId)}" data-sf-selection-focus-type="${attr(focusType)}">${text(sourceTitle)} → ${text(edge.type)} → ${text(targetTitle)}</button>` : `<span>${text(sourceTitle)} → ${text(edge.type)} → ${text(targetTitle)}</span>`}<small>${text(statusLabel(edge.status || 'CANON'))}${external ? ' · outside selection' : ''}</small></li>`;
    };
    return `<div class="sf-inspector-section sf-selection-projection"><h4>语义流摘要</h4><div class="sf-selection-summary-grid"><div><b>${text(summary.nodeCount ?? nodes.length)}</b><span>nodes</span></div><div><b>${text(summary.internalEdgeCount ?? internalEdges.length)}</b><span>inside edges</span></div><div><b>${text(summary.externalEdgeCount ?? externalEdges.length)}</b><span>outbound edges</span></div></div><dl class="sf-kv"><dt>节点类型</dt><dd>${text(typeCounts || '未记录')}</dd><dt>状态</dt><dd>${text(statusCounts || '未记录')}</dd><dt>内部语义</dt><dd>${text(edgeCounts || '未记录')}</dd><dt>选区外连接</dt><dd>${text(externalCounts || '未记录')}</dd><dt>章节范围</dt><dd>${text(summary.chapterFrom != null ? `Ch.${summary.chapterFrom}–${summary.chapterTo}` : '未记录')}</dd><dt>来源</dt><dd>${text(projection.meta?.canonicalSource || 'sqlite.story_graph_projection')}</dd></dl>${internalEdges.length ? `<details open><summary>选区内语义流 (${text(internalEdges.length)}${projection.meta?.internalEdgesTruncated ? '+' : ''})</summary><ul class="sf-inspector-list sf-selection-edge-list">${internalEdges.slice(0, 12).map((edge) => edgeRow(edge)).join('')}</ul></details>` : '<p class="dim-note">选区内没有已记录的语义边；这些节点仍可作为新的规划 Intent 输入。</p>'}${externalEdges.length ? `<details><summary>选区外连接 (${text(externalEdges.length)}${projection.meta?.externalEdgesTruncated ? '+' : ''})</summary><ul class="sf-inspector-list sf-selection-edge-list">${externalEdges.slice(0, 12).map((edge) => edgeRow(edge, true)).join('')}</ul></details>` : ''}<p class="dim-note">选区外连接可点击定位远端节点；不会把未加载的节点伪装成当前画布事实。</p></div>`;
  }

  async function loadSelectionProjection(nodes) {
    if (!state || !S.book || !nodes?.length) return;
    const nodeIds = nodes.map((node) => node.id);
    state.selectionProjection = { nodeIds, loading: true, error: null };
    renderInspector();
    try {
      // Keep the Inspector's first edge page small enough for progressive
      // disclosure on high-degree selections; the API still accepts an
      // explicit larger page for non-UI callers.
      const query = new URLSearchParams({ nodeIds: nodeIds.join(','), limit: '120', edgeLimit: '60' });
      const result = await api('GET', `/books/${currentBook()}/story-graph/selection?${query.toString()}`);
      if (!state || !selectionProjectionMatches(selectedNodes(), result)) return;
      state.selectionProjection = { ...result, loading: false, error: null };
    } catch (error) {
      if (!state || !selectionProjectionMatches(selectedNodes(), { nodeIds })) return;
      state.selectionProjection = { nodeIds, loading: false, error: error.message };
    }
    renderInspector();
  }

  async function loadMoreSelectionEdges() {
    const projection = state?.selectionProjection;
    const nodes = selectedNodes();
    const page = projection?.meta?.externalEdgesPage || projection?.externalEdgesPage;
    const pageToken = page?.nextPageToken;
    if (!projection || !nodes.length || !page?.hasMore || !pageToken) return;
    const nodeIds = nodes.map((node) => node.id);
    if (!selectionProjectionMatches(nodes, projection)) return;
    state.selectionProjection = { ...projection, externalEdgesLoading: true };
    renderInspector();
    try {
      const query = new URLSearchParams({
        nodeIds: nodeIds.join(','),
        limit: '120',
        edgeLimit: String(page.limit || 240),
        externalPageToken: pageToken,
      });
      const result = await api('GET', `/books/${currentBook()}/story-graph/selection?${query.toString()}`);
      if (
        !state
        || !selectionProjectionMatches(selectedNodes(), result)
        || (state.selectionProjection?.meta?.externalEdgesPage || state.selectionProjection?.externalEdgesPage)?.nextPageToken !== pageToken
      ) return;
      const edgeKey = (edge) => edge.id || `${edge.source}|${edge.type}|${edge.target}|${edge.label || ''}`;
      const merged = new Map(
        [...(projection.externalEdges || []), ...(result.externalEdges || [])]
          .map((edge) => [edgeKey(edge), edge]),
      );
      state.selectionProjection = {
        ...result,
        externalEdges: [...merged.values()],
        externalEdgesLoading: false,
        error: null,
      };
    } catch (error) {
      if (
        state
        && selectionProjectionMatches(selectedNodes(), { nodeIds })
        && (state.selectionProjection?.meta?.externalEdgesPage || state.selectionProjection?.externalEdgesPage)?.nextPageToken === pageToken
      ) {
        state.selectionProjection = {
          ...projection,
          externalEdgesLoading: false,
          error: error.message,
        };
      }
    }
    renderInspector();
  }

  function renderContextSourceInspector(node) {
    const metadata = node.metadata || {};
    const included = metadata.included !== false;
    const sourceType = metadata.sourceType || node.subtype || 'context';
    const excludedReason = metadata.excludedReason || 'The manifest recorded this source outside the Writer input.';
    const reason = metadata.inclusionReason || metadata.reason || excludedReason;
    const chapterId = state.contextChapterId || state.context?.chapterId || '';
    const selection = metadata.selection || {};
    const explainability = metadata.explainability || {};
    const semanticEvidence = Array.isArray(selection.edgeTypes) ? selection.edgeTypes : [];
    const range = metadata.persistedPromptRange || metadata.promptRange || metadata.contextRange;
    const rangeText = range
      ? `${range.scope || 'context'} · ${text(range.start)}–${text(range.end)} chars · ${text(range.precision || 'recorded')}`
      : `unavailable${metadata.persistedPromptRangeStatus ? ` · ${text(metadata.persistedPromptRangeStatus)}` : ''}`;
    const tokenEstimate = metadata.contentChars != null
      ? `≈ ${text(Math.round(Number(metadata.contentChars || 0) / 4))} tokens (estimate)`
      : 'unavailable';
    const attribution = metadata.tokenAttribution || {};
    const attributionText = attribution.status === 'estimated'
      ? 'estimated from chars/4; no provider token offsets'
      : attribution.status === 'unavailable'
        ? 'unavailable; source char count not recorded'
        : 'not recorded';
    return `<div class="sf-inspector-head"><div><h3>${text(node.title)}</h3><p>Context Source · ${text(included ? 'INCLUDED' : 'EXCLUDED')}</p></div><span class="sf-status-badge ${included ? 'status-accepted' : 'status-planned'}">${text(included ? 'INCLUDED' : 'EXCLUDED')}</span></div>
      <div class="sf-context-banner ${included ? '' : 'sf-context-excluded'}">${text(reason)}</div>
      <div class="sf-inspector-section"><h4>Manifest provenance</h4><dl class="sf-kv"><dt>GenerationRun</dt><dd>${text(metadata.generationRunId || '—')}</dd><dt>Source type</dt><dd>${text(sourceType)}</dd><dt>Source ID</dt><dd>${text(metadata.sourceId || node.source_id || '—')}</dd><dt>Selection role</dt><dd>${text(metadata.selectionRole || 'not recorded')}</dd><dt>Planned chapter</dt><dd>${text(metadata.plannedChapterNumber ?? '—')}</dd>${semanticEvidence.length ? `<dt>Semantic evidence</dt><dd>${text(semanticEvidence.join(', '))}</dd>` : ''}<dt>Content</dt><dd>${text(metadata.contentChars ?? '—')} chars</dd><dt>Estimate</dt><dd>${tokenEstimate}</dd><dt>Token authority</dt><dd>${text(attributionText)}</dd><dt>Section</dt><dd>${text(metadata.contextSectionTitle || metadata.contextSectionId || '—')}</dd><dt>Prompt location</dt><dd>${text(metadata.promptLocation || 'context')}</dd><dt>Prompt range</dt><dd>${rangeText}</dd></dl></div>
      <div class="sf-inspector-section"><h4>Why this source is here</h4><div class="sf-context-banner ${included ? '' : 'sf-context-excluded'}">${text(explainability.reason || reason)}</div><dl class="sf-kv"><dt>Recorded boundary</dt><dd>${text(explainability.boundary || 'not recorded')}</dd><dt>Focus</dt><dd>${text(explainability.focusNodeId || selection.focusNodeId || 'not recorded')}</dd><dt>Depth</dt><dd>${text(explainability.depth ?? selection.depth ?? 'not recorded')}</dd><dt>Semantic edge types</dt><dd>${text((explainability.semanticEdgeTypes || semanticEvidence).join(', ') || 'not recorded')}</dd></dl><p class="dim-note">Only the persisted GenerationRun manifest is shown. The Canvas does not infer missing causality from layout or prose.</p></div>
      <div class="sf-inspector-section"><h4>Read-only boundary</h4><p class="dim-note">This node is a projection of GenerationRun evidence. It is not a StoryFact and cannot be edited from the canvas.</p></div>
      <div class="sf-inspector-actions">${chapterId ? `<button class="btn btn-sm btn-secondary" data-sf-context-chapter="${attr(chapterId)}">Focus chapter</button>` : ''}<button class="btn btn-sm btn-ghost" data-sf-context-back="1">Back to Context</button></div>`;
  }

  function renderContextEvidence(source) {
    if (!source) return '';
    const included = source.included !== false;
    const reason = included
      ? (source.inclusionReason || source.reason || 'manifest source included')
      : (source.excludedReason || source.reason || 'manifest source excluded');
    const selection = source.selection || {};
    const explainability = source.explainability || {};
    const semanticEvidence = explainability.semanticEdgeTypes || selection.edgeTypes || [];
    return `<div class="sf-inspector-section sf-context-evidence"><h4>Context Explainability</h4><div class="sf-context-banner ${included ? '' : 'sf-context-excluded'}">${text(reason)}</div><dl class="sf-kv"><dt>状态</dt><dd>${text(included ? 'INCLUDED' : 'EXCLUDED')}</dd><dt>Selection role</dt><dd>${text(source.selectionRole || explainability.selectionRole || 'not recorded')}</dd><dt>Planned chapter</dt><dd>${text(source.plannedChapterNumber ?? explainability.plannedChapterNumber ?? '—')}</dd><dt>Manifest section</dt><dd>${text(source.contextSectionTitle || source.contextSectionId || '—')}</dd><dt>Prompt location</dt><dd>${text(source.promptLocation || 'context')}</dd><dt>Recorded boundary</dt><dd>${text(explainability.boundary || 'not recorded')}</dd>${selection.focusNodeId || explainability.focusNodeId ? `<dt>Selection</dt><dd>${text(selection.focusNodeId || explainability.focusNodeId)} · depth ${text(selection.depth ?? explainability.depth ?? '—')}</dd>` : ''}${semanticEvidence.length ? `<dt>Semantic evidence</dt><dd>${text(semanticEvidence.join(', '))}</dd>` : ''}</dl><p class="dim-note">该解释来自 GenerationRun manifest 的记录字段；没有记录的因果关系不会由前端推断。</p></div>`;
  }

  function renderChapterWorkflowEvidence(node, neighbors, pagination) {
    const groups = [
      { title: '人物与势力', types: new Set(['Character', 'Faction']) },
      { title: '地点', types: new Set(['Location']) },
      { title: '事件与场景', types: new Set(['Event', 'Scene']) },
      { title: '剧情线与冲突', types: new Set(['PlotThread', 'Conflict', 'StoryGoal']) },
      { title: '伏笔与秘密', types: new Set(['Foreshadow', 'Secret']) },
      { title: '时间与设定', types: new Set(['TimelinePoint', 'StoryBibleEntry', 'Knowledge']) },
    ];
    const inputEdges = new Set(['depends_on', 'blocks', 'planned_for', 'originates_from', 'mentioned_in', 'included_in_context', 'excluded_from_context']);
    const outputEdges = new Set(['changes', 'reveals', 'advances', 'resolves', 'causes', 'triggers', 'foreshadows', 'affects']);
    const records = Array.isArray(neighbors) ? neighbors.filter((item) => item?.node?.id && item.node.id !== node.id) : [];
    const edgeLabel = (item) => {
      const direction = item.direction === 'in' ? '←' : item.direction === 'out' ? '→' : '↔';
      return `${direction} ${item.edge?.label || item.edge?.type || 'semantic'}`;
    };
    const row = (item) => `<li class="sf-chapter-evidence-row"><button data-sf-neighbor="${attr(item.node.id)}"><span class="sf-evidence-node-title">${text(nodeLabel(item.node.type))} · ${text(item.node.title)}</span><small>${text(statusLabel(item.node.status))}</small></button><span class="sf-neighbor-edge">${text(edgeLabel(item))}</span></li>`;
    const groupMarkup = groups.map((group) => {
      const items = records.filter((item) => group.types.has(item.node.type));
      if (!items.length) return '';
      return `<div class="sf-evidence-group"><div class="sf-evidence-group-head"><span>${text(group.title)}</span><b>${items.length}</b></div><ul class="sf-inspector-list">${items.slice(0, 8).map(row).join('')}</ul>${items.length > 8 ? `<p class="dim-note">还有 ${items.length - 8} 条，见下方语义关系。</p>` : ''}</div>`;
    }).join('');
    const dependencyItems = records.filter((item) => inputEdges.has(String(item.edge?.type || '')));
    const changeItems = records.filter((item) => outputEdges.has(String(item.edge?.type || '')) || item.node.type === 'Fact' || item.node.type === 'StoryState' || item.node.type === 'Relationship');
    const workflowBlock = (title, items, empty) => `<div class="sf-evidence-workflow"><div class="sf-evidence-group-head"><span>${text(title)}</span><b>${items.length}</b></div>${items.length ? `<ul class="sf-inspector-list">${items.slice(0, 8).map(row).join('')}</ul>` : `<p class="dim-note">${text(empty)}</p>`}</div>`;
    const total = Number(pagination?.total || records.length);
    const loaded = records.length;
    const pagingNote = total > loaded
      ? `已读取 ${loaded}/${total} 条一阶 SQLite 证据；使用“加载更多邻居”查看其余记录。`
      : `来自 SQLite Story Graph 的 ${total} 条一阶语义证据。`;
    return `<div class="sf-inspector-section sf-chapter-evidence"><h4>本章工作流证据</h4><p class="dim-note">${text(pagingNote)} 这些是已投影的来源关系，不是前端根据章节文本推断出的事实。</p>${groupMarkup ? `<div class="sf-evidence-group-grid">${groupMarkup}</div>` : '<p class="dim-note">当前章节没有已投影的一阶人物、地点、事件或剧情结构关系。</p>'}<div class="sf-evidence-workflow-grid">${workflowBlock('本章依赖 / 输入', dependencyItems, '没有记录到 depends_on、blocks 或计划输入关系。')}${workflowBlock('本章改变 / 输出', changeItems, '没有记录到 StoryFact、StoryState 或语义变化关系。')}</div></div>`;
  }

  function renderInspectorNode(node, detail) {
    if (node.type === 'ContextSource') return renderContextSourceInspector(node);
    const metadata = node.metadata || {};
    const stateData = metadata.state || metadata.characterState || {};
    const neighbors = detail?.neighbors || [];
    const provenance = node.provenance || [];
    const chapter = metadata.number || metadata.chapterNumber || metadata.narrativeOrder || node.chapter_id || '—';
    const status = statusLabel(node.status);
    const knowledgeEntries = Array.isArray(metadata.knowledgeEntries) ? metadata.knowledgeEntries : [];
    const knowledge = knowledgeEntries.length
      ? knowledgeEntries
      : (Array.isArray(stateData.knowledge) ? stateData.knowledge : Array.isArray(metadata.knowledge) ? metadata.knowledge : []).map((item) => ({ text: item, status: 'known' }));
    const knownKnowledge = knowledge.filter((item) => String(item?.status || 'known').toLowerCase() === 'known');
    const unknownKnowledge = knowledge.filter((item) => String(item?.status || '').toLowerCase() === 'unknown');
    const knowledgeEvidenceRows = (items) => items.slice(0, 12).map((item) => {
      const itemMetadata = item?.metadata || {};
      const sourceChapter = itemMetadata.sourceChapter || itemMetadata.chapterNumber;
      const confidence = itemMetadata.confidence;
      const evidence = [
        sourceChapter ? `Ch ${sourceChapter}` : '',
        confidence != null ? `confidence ${confidence}` : 'character_states.knowledge',
      ].filter(Boolean).join(' · ');
      return `<li class="sf-knowledge-row"><span>${text(item.text || item.name || item.title || JSON.stringify(item))}</span><small>${text(evidence)}</small></li>`;
    }).join('');
   const relationSummary = neighbors.slice(0, 12).map((item) => `<div class="sf-neighbor-row"><button data-sf-neighbor="${attr(item.node.id)}">${text(nodeLabel(item.node.type))} · ${text(item.node.title)}</button><span class="sf-neighbor-edge">${text(item.edge.label || item.edge.type)}</span></div>`).join('');
    const boundaryEdges = viewportBoundaryEdgesForNode(node.id);
    const boundaryMeta = state.graph?.meta?.viewport || {};
    const boundaryCount = Number(boundaryMeta.crossBoundaryEdgeCount || 0);
    const boundaryRows = boundaryEdges.slice(0, 12).map((item) => {
      const remote = item.remoteEndpoint || {};
      const direction = item.source === node.id ? '→' : '←';
      return `<div class="sf-neighbor-row sf-boundary-neighbor-row"><button data-sf-boundary-node="${attr(remote.id || '')}">${text(nodeLabel(remote.type || 'Node'))} · ${text(remote.title || remote.id || 'unavailable')}</button><span class="sf-neighbor-edge">${text(direction)} ${text(item.label || item.type || 'semantic')}</span></div>`;
    }).join('');
    const boundaryDetails = state.view === 'all' && boundaryMeta.requested && boundaryCount
      ? `<div class="sf-inspector-section sf-viewport-boundary"><h4>Cross-viewport semantic edges · ${text(boundaryCount)}</h4><div class="sf-context-banner"><b>These are recorded SQLite relationships.</b><br>They cross the current world-coordinate page and are not drawn until both endpoints are loaded. Select a remote endpoint to focus and expand it.${boundaryMeta.crossBoundaryEdgesTruncated ? '<br><small>Only a bounded sample is shown; the count is complete.</small>' : ''}</div>${boundaryRows ? `<div class="sf-boundary-neighbor-list">${boundaryRows}</div>` : '<p class="dim-note">The current node is not in the returned boundary sample. Load its full neighbor page to inspect every edge.</p>'}</div>`
      : '';
   const impactable = ['Chapter', 'Character', 'Foreshadow', 'PlotThread', 'Fact', 'Location', 'StoryBibleEntry', 'Scene', 'Item', 'Secret', 'StoryGoal', 'Conflict', 'TimelinePoint', 'Knowledge'].includes(node.type);
    const neighborCount = detail?.pagination?.total != null ? ` · ${detail.pagination.total} 条` : '';
    const acceptedChapterNumber = metadata.acceptedChapterNumber || metadata.chapterNumber || metadata.acceptedChapterId;
    const contextEvidence = state.contextEvidence?.nodeId === node.id ? renderContextEvidence(state.contextEvidence) : '';
    const controlHistory = Array.isArray(metadata.controlHistory) ? metadata.controlHistory : [];
    const characterDetails = node.type === 'Character'
      ? (() => {
        const relationshipTypes = new Set(['trusts', 'suspects', 'allies_with', 'hostile_to', 'member_of', 'connects', 'interacts_with']);
        const relationshipNeighbors = neighbors.filter((item) => ['Character', 'Faction'].includes(item.node?.type) && relationshipTypes.has(item.edge?.type));
        const plotThreadNeighbors = neighbors.filter((item) => item.node?.type === 'PlotThread');
        const foreshadowNeighbors = neighbors.filter((item) => item.node?.type === 'Foreshadow');
        const recentAppearanceChapters = Array.isArray(metadata.recentAppearanceChapters)
          ? metadata.recentAppearanceChapters
          : (Array.isArray(metadata.appearanceChapters) ? metadata.appearanceChapters.slice(-8) : []);
        const stateStatus = metadata.state_status || stateData.status || '未记录';
        const stateLocation = metadata.current_location || stateData.location || '未记录';
        const emotionalState = metadata.emotional_state || stateData.emotional_state || '未记录';
        const stateChapter = stateData.chapter_number || metadata.lastAppearanceChapter || '未记录';
        const neighborRows = (items, empty) => items.length
          ? items.slice(0, 8).map((item) => `<li><button data-sf-neighbor="${attr(item.node.id)}">${text(item.node.title)}</button><span>${text(item.edge?.label || item.edge?.type || nodeLabel(item.node.type))}</span></li>`).join('')
          : `<li class="dim-note">${empty}</li>`;
        const appearanceRows = recentAppearanceChapters.length
          ? recentAppearanceChapters.slice().reverse().map((number) => `<li><b>Ch ${text(number)}</b><span>最近出现</span></li>`).join('')
          : '<li class="dim-note">尚无结构化出现记录</li>';
        return `<div class="sf-inspector-section sf-character-state"><h4>人物当前状态</h4><dl class="sf-kv"><dt>状态</dt><dd>${text(stateStatus)}</dd><dt>当前位置</dt><dd>${text(stateLocation)}</dd><dt>情绪</dt><dd>${text(emotionalState)}</dd><dt>状态来源</dt><dd>${text(stateChapter === '未记录' ? stateChapter : `Ch ${stateChapter}`)}</dd><dt>最近出现</dt><dd>${text(metadata.lastAppearanceChapter ? `Ch ${metadata.lastAppearanceChapter}` : '未记录')}</dd></dl></div><div class="sf-inspector-section"><h4>关系与势力</h4><ul class="sf-inspector-list">${neighborRows(relationshipNeighbors, '尚无结构化关系或势力边')}</ul></div><div class="sf-inspector-section"><h4>参与剧情线</h4><ul class="sf-inspector-list">${neighborRows(plotThreadNeighbors, '当前焦点子图没有关联剧情线')}</ul></div><div class="sf-inspector-section"><h4>关联伏笔</h4><ul class="sf-inspector-list">${neighborRows(foreshadowNeighbors, '当前焦点子图没有关联伏笔')}</ul></div><div class="sf-inspector-section"><h4>最近出现章节</h4><ul class="sf-inspector-list">${appearanceRows}</ul></div>`;
      })()
      : '';
    const worldDetails = node.type === 'World'
      ? `<div class="sf-inspector-section"><h4>World Graph 根节点</h4><div class="sf-context-banner">这是从作品 Book 投影出的层级根，不是新的 Canon 表。当前视图不假设空间坐标，也不会把线性排列伪装成地图。</div><dl class="sf-kv"><dt>层级</dt><dd>World → Region → City → Location</dd><dt>空间地图</dt><dd>${text(metadata.spatialMapAvailable ? '已配置坐标' : '未配置 · 当前为 World Graph')}</dd><dt>叠加事实</dt><dd>${text((metadata.overlayEdges || []).join(', ') || 'controls, present_at, happens_at')}</dd></dl></div>`
      : node.type === 'Location'
        ? `<div class="sf-inspector-section"><h4>地点层级与状态</h4><dl class="sf-kv"><dt>层级</dt><dd>${text(metadata.hierarchyLevelLabel || metadata.hierarchyLevel || 'Location')}</dd><dt>路径</dt><dd>${text((metadata.hierarchyPath || []).join(' → ') || node.title)}</dd><dt>当前控制</dt><dd>${text(metadata.currentControlLabel || metadata.currentControl || '未记录')}</dd><dt>控制记录</dt><dd>${text(controlHistory.length ? `${controlHistory.length} 条 location_states` : '未记录')}</dd><dt>空间地图</dt><dd>${text(metadata.spatialMapAvailable ? '已配置坐标' : '未配置 · 层级图')}</dd></dl></div>`
        : '';
    const foreshadowDetails = node.type === 'Foreshadow'
      ? (() => {
        const lifecycle = Array.isArray(metadata.lifecycleEvents) ? metadata.lifecycleEvents : [];
        const lifecycleLabels = { planted: '埋下', advanced: '推进', deferred: '延期', resolved: '回收' };
        const lifecycleRows = lifecycle.length
          ? lifecycle.map((item) => `<li><strong>${text(lifecycleLabels[item.action] || item.action || '生命周期')}</strong><span>第 ${text(item.chapterNumber ?? '—')} 章 · ${text(statusLabel(item.status || 'CANON'))}</span>${item.factId ? `<code>${text(item.factId)}</code>` : ''}</li>`).join('')
          : '<li>尚无结构化生命周期记录</li>';
        const related = Array.isArray(metadata.relatedEntities) ? metadata.relatedEntities : [];
        const relatedRows = related.length
          ? related.slice(0, 12).map((item) => `<li>${text(nodeById(item.id)?.title || item.id || '—')}<span>${text(nodeLabel(item.type || 'Entity'))}</span></li>`).join('')
          : '<li>尚无结构化关联</li>';
        return `<div class="sf-inspector-section"><h4>伏笔生命周期</h4><dl class="sf-kv"><dt>当前阶段</dt><dd>${text(lifecycleLabels[metadata.currentStage] || metadata.currentStage || '未记录')}</dd><dt>推进章节</dt><dd>${text((metadata.advanceChapters || []).join(', ') || '—')}</dd></dl><ol class="sf-lifecycle-list">${lifecycleRows}</ol></div><div class="sf-inspector-section"><h4>关联实体</h4><ul class="sf-inspector-list">${relatedRows}</ul></div>`;
      })()
      : '';
    const plotThreadDetails = node.type === 'PlotThread'
      ? (() => {
        const lifecycle = Array.isArray(metadata.lifecycleEvents) ? metadata.lifecycleEvents : [];
        const lifecycleLabels = { planted: '起源', advanced: '推进', deferred: '延期', resolved: '回收' };
        const lifecycleRows = lifecycle.length
          ? lifecycle.map((item) => `<li><strong>${text(lifecycleLabels[item.action] || item.action || '生命周期')}</strong><span>第 ${text(item.chapterNumber ?? '—')} 章 · ${text(statusLabel(item.status || 'CANON'))}</span>${item.factId ? `<code>${text(item.factId)}</code>` : ''}</li>`).join('')
          : '<li>尚无显式剧情线生命周期事实</li>';
        const related = Array.isArray(metadata.relatedEntities) ? metadata.relatedEntities : [];
        const relatedRows = related.length
          ? related.slice(0, 12).map((item) => `<li>${text(nodeById(item.id)?.title || item.id || '—')}<span>${text(nodeLabel(item.type || 'Entity'))}${item.chapterNumbers?.length ? ` · Ch ${text(item.chapterNumbers.join(', '))}` : ''}</span></li>`).join('')
          : '<li>尚无结构化关联</li>';
        const evidenceLabel = metadata.lifecycleEvidence === 'explicit_story_fact_action'
          ? '已由显式 StoryFact action 建立'
          : '当前只有 typed reference 关联，尚无剧情线进度证据';
        return `<div class="sf-inspector-section"><h4>剧情线生命周期</h4><div class="sf-context-banner">${text(evidenceLabel)}</div><dl class="sf-kv"><dt>当前阶段</dt><dd>${text(lifecycleLabels[metadata.currentStage] || metadata.currentStage || '未记录')}</dd><dt>起源章节</dt><dd>${text((metadata.originChapters || []).join(', ') || '—')}</dd><dt>推进章节</dt><dd>${text((metadata.advanceChapters || []).join(', ') || '—')}</dd><dt>回收章节</dt><dd>${text((metadata.resolveChapters || []).join(', ') || '—')}</dd></dl><ol class="sf-lifecycle-list">${lifecycleRows}</ol></div><div class="sf-inspector-section"><h4>关联实体</h4><ul class="sf-inspector-list">${relatedRows}</ul></div>`;
      })()
      : '';
    const storyBibleDetails = node.type === 'StoryBibleEntry'
      ? (() => {
        const subtype = String(metadata.subtype || 'entry');
        const isPublished = subtype === 'published-snapshot' && metadata.isCurrentPublished === true;
        const payloadKeys = Array.isArray(metadata.payloadKeys)
          ? metadata.payloadKeys
          : (Array.isArray(metadata.stepKeys) ? metadata.stepKeys : []);
        const boundary = metadata.provenanceBoundary || 'story_bible_projection';
        const statusNote = isPublished
          ? '这是当前已发布快照，属于 Canon 规划边界。'
          : subtype === 'draft-snapshot'
            ? '这是最近一次草稿快照，尚未成为 Canon。'
            : metadata.stepStatus === 'confirmed'
              ? '这是已确认但尚未发布的规划步骤；它不会覆盖当前 Canon。'
              : '这是 Story Bible 草稿步骤；它仍处于规划编辑边界。';
        return `<div class="sf-inspector-section"><h4>Story Bible 事实边界</h4><div class="sf-context-banner ${isPublished ? '' : 'sf-context-excluded'}">${text(statusNote)}</div><dl class="sf-kv"><dt>投影子类型</dt><dd>${text(subtype)}</dd>${metadata.workspaceStatus ? `<dt>工作区</dt><dd>${text(metadata.workspaceStatus)}</dd>` : ''}${metadata.snapshotVersion != null ? `<dt>快照版本</dt><dd>${text(metadata.snapshotVersion)}</dd>` : ''}${metadata.stepNumber != null ? `<dt>步骤</dt><dd>${text(metadata.stepNumber)} · ${text(metadata.stepKey || '—')}</dd>` : ''}${metadata.stepStatus ? `<dt>步骤状态</dt><dd>${text(metadata.stepStatus)}</dd>` : ''}${metadata.source ? `<dt>来源</dt><dd>${text(metadata.source)}</dd>` : ''}<dt>Provenance boundary</dt><dd>${text(boundary)}</dd>${payloadKeys.length ? `<dt>载荷字段</dt><dd>${text(payloadKeys.join(', '))}</dd>` : ''}${metadata.payloadChars != null ? `<dt>快照载荷</dt><dd>${text(metadata.payloadChars)} chars</dd>` : ''}</dl>${metadata.payloadSummary ? `<div class="sf-provenance sf-story-bible-summary">${text(metadata.payloadSummary)}</div>` : ''}</div>`;
      })()
      : '';
    const evidenceReferenceDetails = metadata.derived && metadata.referenceType
      ? (() => {
        const sources = Array.isArray(metadata.referenceSources) ? metadata.referenceSources : [];
        const sourceRows = sources.slice(0, 8).map((item) => `<li><code>${text(item.table || 'sqlite')}</code><span>${text(item.id || metadata.sourceRecordId || 'source record')}</span>${item.chapterId ? `<small>Ch ${text(item.chapterId)}</small>` : ''}</li>`).join('');
        return `<div class="sf-inspector-section"><h4>Typed story evidence</h4><div class="sf-context-banner">This node is a read-model projection from an explicit structured reference. It is not a new Canon table.</div><dl class="sf-kv"><dt>Reference type</dt><dd>${text(metadata.referenceType)}</dd><dt>Reference ID</dt><dd><code>${text(metadata.referenceId || node.source_id || '—')}</code></dd><dt>Source boundary</dt><dd>${text(node.source_type || 'SQLite')}</dd><dt>Projection</dt><dd>${text(metadata.projection || 'typed_story_fact_reference')}</dd></dl>${sourceRows ? `<ul class="sf-inspector-list">${sourceRows}</ul>` : '<p class="dim-note">No structured source record was retained.</p>'}</div>`;
      })()
      : '';
    const chapterWorkflowEvidence = node.type === 'Chapter'
      ? detail
        ? renderChapterWorkflowEvidence(node, neighbors, detail.pagination)
        : '<div class="sf-inspector-section sf-chapter-evidence"><h4>本章工作流证据</h4><p class="dim-note">正在从 SQLite 读取人物、地点、事件、伏笔和事实关系…</p></div>'
      : '';
    const diagnostics = Array.isArray(metadata.graphDiagnostics) ? metadata.graphDiagnostics : [];
    const diagnosticBlock = diagnostics.length
      ? `<div class="sf-context-banner ${node.status === 'CONFLICT' ? 'sf-context-excluded' : ''}"><b>${text(statusLabel(node.status))}</b> · ${text(metadata.graphStatusReason || diagnostics[0].message || 'projection diagnostic')}<ul class="sf-diagnostic-list">${diagnostics.slice(0, 6).map((item) => `<li><code>${text(item.code || 'diagnostic')}</code> ${text(item.message || '')}${item.reviewIssueIds?.length ? ` · issues ${text(item.reviewIssueIds.join(', '))}` : ''}</li>`).join('')}</ul><small>这是只读投影诊断；修复应通过章节版本、Review 或 StoryCommit 完成。</small></div>`
      : '';
    const canReforecastCandidate = !!state.editMode
      && !!metadata.candidateSetId
      && !!metadata.candidateBranchId
      && !!node.id;
    const candidateBranch = metadata.candidateBranchId
      ? `<div class="sf-inspector-section"><h4>Candidate Branch</h4><dl class="sf-kv"><dt>Set</dt><dd>${text(metadata.candidateSetId || 'legacy lineage fallback')}</dd><dt>Branch</dt><dd>${text(metadata.candidateBranchId)}</dd><dt>Position</dt><dd>${text(metadata.branchIndex || '—')} / ${text(metadata.branchCount || '—')}</dd><dt>Origin</dt><dd>${text(metadata.originNodeId || '—')}</dd><dt>GenerationRun</dt><dd>${text(metadata.generationRunId || '—')}</dd>${metadata.sourceAnalysisTaskId ? `<dt>Based on analysis</dt><dd>${text(metadata.sourceAnalysisTaskId)}</dd>` : ''}${metadata.sourceAnalysisGenerationRunId ? `<dt>Analysis Run</dt><dd>${text(metadata.sourceAnalysisGenerationRunId)}</dd>` : ''}${metadata.sourceCandidateSetId ? `<dt>Parent candidate set</dt><dd>${text(metadata.sourceCandidateSetId)}</dd>` : ''}${metadata.sourceCandidateBranchId ? `<dt>Parent branch</dt><dd>${text(metadata.sourceCandidateBranchId)}</dd>` : ''}${metadata.sourceCandidateRootNodeId ? `<dt>Parent root</dt><dd>${text(metadata.sourceCandidateRootNodeId)}</dd>` : ''}<dt>Decision</dt><dd>${text(metadata.candidateDecision || metadata.candidateBranchStatus || 'CANDIDATE')}</dd></dl><p class="dim-note">Candidate alternatives are grouped by this set. Select a branch row to focus it, then use Adopt / Discard. Decisions are revisioned planning state and never write StoryFact.</p><div class="sf-inspector-actions"><button class="btn btn-sm btn-ghost" data-sf-candidate-lineage="1">查看谱系</button>${metadata.generationRunId ? `<button class="btn btn-sm btn-ghost" data-sf-generation-trace="${attr(metadata.generationRunId)}">查看生成上下文</button>` : ''}${canReforecastCandidate ? `<button class="btn btn-sm btn-secondary" data-sf-candidate-reforecast="1">从此分支重新推演</button>` : ''}</div></div>`
      : '';
    const generationRunId = String(metadata.generationRunId || '');
    const generationTrace = state.generationRunTrace?.runId === generationRunId ? state.generationRunTrace : null;
    const generationContextGraph = state.generationContextGraph?.runId === generationRunId
      ? state.generationContextGraph
      : null;
    const generationRunDetails = generationRunId
      ? `<div class="sf-inspector-section"><h4>GenerationRun provenance</h4>${!generationTrace ? '<p class="dim-note">点击“查看生成上下文”读取安全的运行摘要。</p>' : generationTrace.loading ? '<p class="dim-note">正在读取 SQLite GenerationRun 摘要…</p>' : generationTrace.error ? `<p class="dim-note">${text(generationTrace.error)}</p>` : (() => { const run = generationTrace.selectedRun || {}; const context = run.context || {}; return `<div class="sf-context-banner">只显示运行元数据、来源类型和计数；不会显示 Prompt 正文或凭据。</div><dl class="sf-kv"><dt>Task</dt><dd>${text(run.taskId || generationTrace.taskId || '—')}</dd><dt>Agent</dt><dd>${text(run.agentRole || '—')}</dd><dt>Provider / model</dt><dd>${text(run.provider?.name || run.provider?.id || '—')} / ${text(run.model?.name || run.model?.id || '—')}</dd><dt>Sources</dt><dd>${text(context.includedItems ?? 0)} included / ${text(context.excludedItems ?? 0)} excluded</dd><dt>Types</dt><dd>${text(context.sourceTypes?.join(', ') || '—')}</dd><dt>Prompt ranges</dt><dd>${text(context.exactPersistedPromptRanges ?? 0)} exact</dd></dl>`; })()}</div>`
      : '';
    const fulfillment = node.type === 'PlanningNode' && metadata.acceptedChapterId
      ? `<div class="sf-inspector-section"><h4>Canon 兑现</h4><div class="sf-context-banner">该 StoryFlow 计划已由 StoryCommit 接受并兑现为第 ${text(acceptedChapterNumber)} 章。</div><dl class="sf-kv"><dt>章节节点</dt><dd>${text(`chapter:${metadata.acceptedChapterId}`)}</dd><dt>StoryCommit</dt><dd>${text(metadata.storyCommitId || '—')}</dd><dt>接受时间</dt><dd>${text(metadata.acceptedAt || '—')}</dd></dl></div>`
      : '';
    const generationContextGraphDetails = generationRunId
      ? generationContextGraph
        ? renderGenerationContextGraphSection(generationContextGraph, 'AI Context Graph')
        : generationTrace?.selectedRun?.context?.contextGraphSnapshot?.available
          ? `<div class="sf-inspector-section sf-context-graph-evidence"><h4>AI Context Graph</h4><p class="dim-note">A metadata-only Context Graph snapshot is persisted for this GenerationRun. Load it to inspect the exact source nodes and semantic edges used by the AI action.</p><button class="btn btn-sm btn-secondary" data-sf-context-graph-load="${attr(generationRunId)}">View Context Graph</button></div>`
          : generationTrace && !generationTrace.loading && !generationTrace.error
            ? '<div class="sf-inspector-section sf-context-graph-evidence"><h4>AI Context Graph</h4><p class="dim-note">No persisted Context Graph snapshot is available for this run. The UI will not infer AI context from the current Story Graph or prompt text.</p></div>'
            : ''
      : '';
    const chapterActions = node.type === 'Chapter'
      ? '<button class="btn btn-sm btn-secondary" data-sf-inspector-action="audit">审查</button><button class="btn btn-sm btn-secondary" data-sf-inspector-action="rewrite">重写</button><button class="btn btn-sm btn-secondary" data-sf-inspector-action="versions">查看版本</button><button class="btn btn-sm btn-secondary" data-sf-inspector-action="chapter-impact">编辑影响</button>'
      : '';
    const characterActions = node.type === 'Character'
      ? `<button class="btn btn-sm btn-secondary" data-sf-inspector-action="timeline">查看时间线</button><button class="btn btn-sm btn-secondary" data-sf-inspector-action="character-analyze" ${modelRuntimeReady() ? '' : 'disabled aria-disabled="true"'}>AI 分析</button>`
      : '';
    const openLabel = node.type === 'Chapter' ? '打开章节' : node.type === 'StoryBibleEntry' ? '打开 Story Bible' : '打开来源';
     return `<div class="sf-inspector-head"><div><h3>${text(node.title)}</h3><p>${text(nodeLabel(node.type))} · ${text(status)}</p></div><span class="sf-status-badge ${statusClass(node.status)}">${text(status)}</span></div>
       ${diagnosticBlock}
       <div class="sf-inspector-actions"><button class="btn btn-sm btn-secondary" data-sf-inspector-action="focus">聚焦</button><button class="btn btn-sm btn-secondary" data-sf-inspector-action="expand">展开二阶</button>${node.type === 'Chapter' ? '<button class="btn btn-sm btn-secondary" data-sf-inspector-action="context">查看 Context</button>' : ''}${chapterActions}${characterActions}${impactable ? '<button class="btn btn-sm btn-secondary" data-sf-inspector-action="impact">影响分析</button>' : ''}<button class="btn btn-sm btn-secondary" data-sf-inspector-action="open">${openLabel}</button></div>
       <div class="sf-inspector-section"><h4>创作状态</h4><dl class="sf-kv"><dt>摘要</dt><dd>${text(node.summary || '暂无摘要')}</dd><dt>章节</dt><dd>${text(chapter)}</dd><dt>来源表</dt><dd>${text(node.source_type || '—')}</dd><dt>来源 ID</dt><dd>${text(node.source_id || '—')}</dd>${metadata.storyTime || metadata.event_time ? `<dt>故事时间</dt><dd>${text(metadata.storyTime || metadata.event_time)}</dd>` : ''}${metadata.current_location ? `<dt>当前位置</dt><dd>${text(metadata.current_location)}</dd>` : ''}</dl></div>
       ${characterDetails}
       ${chapterWorkflowEvidence}
       ${worldDetails}
       ${foreshadowDetails}
       ${plotThreadDetails}
       ${storyBibleDetails}
       ${evidenceReferenceDetails}
       ${candidateBranch}
      ${generationRunDetails}
      ${generationContextGraphDetails}
      ${fulfillment}
      ${renderReconciliationBlock(node)}
      ${contextEvidence}
      ${knownKnowledge.length ? `<div class="sf-inspector-section"><h4>她/他知道 (${knownKnowledge.length})</h4><ul class="sf-inspector-list">${knowledgeEvidenceRows(knownKnowledge)}</ul><p class="dim-note">仅展示 SQLite character_states.knowledge 的显式记录。</p></div>` : ''}
     ${unknownKnowledge.length ? `<div class="sf-inspector-section"><h4>她/他不知道 (${unknownKnowledge.length})</h4><ul class="sf-inspector-list sf-inspector-list-unknown">${knowledgeEvidenceRows(unknownKnowledge)}</ul><p class="dim-note">未知状态必须有显式记录；没有记录不等于人物知道全部信息。</p></div>` : ''}
     <div class="sf-inspector-section"><h4>语义关系 ${detail ? `${neighbors.length}${neighborCount}` : ''}</h4>${detail ? (relationSummary || '<p class="dim-note">当前节点没有已投影的一阶语义边。</p>') : '<p class="dim-note">正在从 SQLite 读取邻接关系…</p>'}</div>
      ${boundaryDetails}
     <div class="sf-inspector-section"><h4>Provenance</h4>${provenance.length ? `<div class="sf-provenance">${provenance.slice(0, 6).map((item) => `<div>· ${text(item.kind || 'source')} ${item.table ? `<code>${text(item.table)}</code>` : ''} ${item.id ? `<code>${text(item.id)}</code>` : ''}</div>`).join('')}</div>` : '<p class="sf-provenance">未记录可展示的来源链。</p>'}</div>`;
  }

  function viewportBoundaryEdgesForNode(nodeId) {
    const viewport = state?.graph?.meta?.viewport;
    if (!viewport?.requested || !nodeId) return [];
    return (Array.isArray(viewport.crossBoundaryEdges) ? viewport.crossBoundaryEdges : [])
      // Boundary means outside the current world-coordinate page.  A remote
      // endpoint may already be cached from an earlier page; it still belongs
      // in the Inspector because it is not part of the current viewport.
      .filter((edge) => edge.loadedEndpointId === nodeId);
  }

  async function loadNodeDetail(nodeId) {
    if (!nodeId || !state || !state.graph) return;
    if (isPresentationCluster(nodeById(nodeId)) || nodeById(nodeId)?.type === 'ContextSource') {
      state.detail = null;
      renderInspector();
      return;
    }
    try {
      const result = await api('GET', `/books/${currentBook()}/story-graph/nodes/${encodeURIComponent(nodeId)}`);
      if (state.selected.has(nodeId)) {
        state.detail = result;
        const generationRunId = result.node?.metadata?.generationRunId || nodeById(nodeId)?.metadata?.generationRunId || '';
        state.generationRunTrace = generationRunId
          ? { runId: String(generationRunId), loading: true }
          : null;
        renderInspector();
        if (generationRunId) loadGenerationRunTrace(String(generationRunId));
        if (result.node?.type === 'Chapter' && state.history?.nodeId !== nodeId) loadHistory(nodeId);
        if (result.node?.type === 'PlanningNode') loadReconciliationCandidates(nodeId);
      }
    } catch (error) {
      if (state.selected.has(nodeId)) {
        state.detailError = error.message;
        renderInspector();
        toast(`节点详情读取失败：${error.message}`, 'error');
      }
    }
  }

  async function loadGenerationRunTrace(generationRunId) {
    if (!state || !generationRunId) return;
    const runId = String(generationRunId);
    state.generationRunTrace = { runId, loading: true };
    renderInspector();
    try {
      const result = await api('GET', `/books/${currentBook()}/story-graph/generation-runs/${encodeURIComponent(runId)}`);
      if (state.generationRunTrace?.runId === runId) {
        state.generationRunTrace = { ...result, runId, loading: false };
        state.generationContextGraph = null;
        renderInspector();
        if (result.selectedRun?.context?.contextGraphSnapshot?.available) {
          loadGenerationRunContextGraph(runId);
        }
      }
    } catch (error) {
      if (state.generationRunTrace?.runId === runId) {
        state.generationRunTrace = { runId, error: error.message };
        renderInspector();
        toast(`GenerationRun 摘要读取失败：${error.message}`, 'warning');
      }
    }
  }

  async function loadGenerationRunContextGraph(generationRunId) {
    if (!state || !generationRunId) return;
    const runId = String(generationRunId);
    if (state.generationContextGraph?.runId === runId && state.generationContextGraph.loading) return;
    state.generationContextGraph = { runId, loading: true };
    renderInspector();
    try {
      const result = await api('GET', `/books/${currentBook()}/story-graph/generation-runs/${encodeURIComponent(runId)}/context-graph`);
      if (state.generationContextGraph?.runId === runId) {
        state.generationContextGraph = { ...result, runId, loading: false };
        renderInspector();
      }
    } catch (error) {
      if (state.generationContextGraph?.runId === runId) {
        state.generationContextGraph = { runId, error: error.message };
        renderInspector();
        toast(`Context Graph read failed: ${error.message}`, 'warning');
      }
    }
  }

  async function loadMoreNeighbors(nodeId) {
    const detail = state?.detail;
    const pagination = detail?.node?.id === nodeId ? detail.pagination : null;
    if (!pagination?.hasMore || state.neighborLoading) return;
    state.neighborLoading = true;
    renderInspector();
    try {
      const continuation = pagination.nextPageToken
        ? `&pageToken=${encodeURIComponent(pagination.nextPageToken)}`
        : `&offset=${pagination.nextOffset}`;
      const result = await api('GET', `/books/${currentBook()}/story-graph/neighbors/${encodeURIComponent(nodeId)}?limit=${pagination.limit}${continuation}`);
      if (state.detail?.node?.id === nodeId) {
        state.detail.neighbors = [...(state.detail.neighbors || []), ...(result.neighbors || [])];
        state.detail.pagination = result.pagination;
      }
    } catch (error) {
      toast(`更多邻居读取失败：${error.message}`, 'error');
    } finally {
      state.neighborLoading = false;
      renderInspector();
    }
  }

  async function loadImpact(nodeId) {
    state.impact = { nodeId, loading: true };
    state.chapterImpact = null;
    renderInspector();
    try {
      state.impact = await api('GET', `/books/${currentBook()}/story-graph/impact/${encodeURIComponent(nodeId)}?depth=2&limit=120`);
      renderInspector();
    } catch (error) {
      state.impact = { nodeId, error: error.message };
      renderInspector();
      toast(`影响分析读取失败：${error.message}`, 'error');
    }
  }

  async function loadChapterEditImpact(nodeId, versionId = '') {
    state.chapterImpact = { nodeId, versionId, loading: true };
    state.impact = null;
    renderInspector();
    try {
      const query = new URLSearchParams({ depth: '3', limit: '120' });
      if (versionId) query.set('versionId', versionId);
      const result = await api('GET', `/books/${currentBook()}/story-graph/chapter-impact/${encodeURIComponent(nodeId)}?${query.toString()}`);
      state.chapterImpact = {
        ...result,
        nodeId,
        versionId: versionId || result.version?.id || '',
      };
      renderInspector();
    } catch (error) {
      state.chapterImpact = { nodeId, versionId, error: error.message };
      renderInspector();
      toast(`章节编辑影响读取失败：${error.message}`, 'error');
    }
  }

  async function loadChapterVersionCompare(nodeId, fromVersionId, toVersionId) {
    const fromId = String(fromVersionId || '').trim();
    const toId = String(toVersionId || '').trim();
    state.chapterVersionCompare = { nodeId, fromVersionId: fromId, toVersionId: toId, loading: true };
    state.chapterImpact = null;
    renderInspector();
    try {
      const query = new URLSearchParams({
        fromVersionId: fromId,
        toVersionId: toId,
        depth: '3',
        limit: '120',
      });
      const result = await api('GET', `/books/${currentBook()}/story-graph/chapter-version-compare/${encodeURIComponent(nodeId)}?${query.toString()}`);
      state.chapterVersionCompare = {
        ...result,
        nodeId,
        fromVersionId: fromId,
        toVersionId: toId,
      };
      renderInspector();
    } catch (error) {
      state.chapterVersionCompare = { nodeId, fromVersionId: fromId, toVersionId: toId, error: error.message };
      renderInspector();
      toast(`Version compare failed: ${error.message}`, 'error');
    }
  }

  async function loadHistory(nodeId) {
    state.history = { nodeId, loading: true };
    state.snapshotDiff = null;
    state.canonicalReplay = null;
    state.canonicalDiff = null;
    state.chapterVersionCompare = null;
    renderInspector();
    try {
      state.history = await api('GET', `/books/${currentBook()}/story-graph/history?nodeId=${encodeURIComponent(nodeId)}&limit=120`);
      renderInspector();
    } catch (error) {
      state.history = { nodeId, error: error.message };
      renderInspector();
      toast(`History read failed: ${error.message}`, 'error');
    }
  }

  async function retryGraphSnapshot(commitId, nodeId) {
    const normalizedCommitId = String(commitId || '').trim();
    if (!normalizedCommitId) return;
    try {
      const result = await api('POST', `/books/${currentBook()}/story-graph/snapshots/retry`, {
        commitId: normalizedCommitId,
      });
      if (result.captured) {
        toast(result.recovered ? 'StoryFlow historical snapshot recovered.' : 'StoryFlow snapshot already exists.', 'success');
      } else {
        toast(result.reason || 'Snapshot was not safely reconstructed.', 'warning');
      }
      await loadHistory(nodeId);
    } catch (error) {
      toast(`StoryFlow snapshot retry failed: ${error.message}`, 'error');
    }
  }

  async function loadSnapshotDiff(fromSnapshot, toSnapshot, nodeId) {
    state.snapshotDiff = { fromSnapshot, toSnapshot, nodeId, loading: true };
    renderInspector();
    try {
      state.snapshotDiff = await api('GET', `/books/${currentBook()}/story-graph/diff?fromSnapshot=${encodeURIComponent(fromSnapshot)}&toSnapshot=${encodeURIComponent(toSnapshot)}${nodeId ? `&nodeId=${encodeURIComponent(nodeId)}` : ''}`);
      renderInspector();
    } catch (error) {
      state.snapshotDiff = { fromSnapshot, toSnapshot, nodeId, error: error.message };
      renderInspector();
      toast(`Graph snapshot diff 读取失败：${error.message}`, 'error');
    }
  }

  async function loadCanonicalReplay(commitId, nodeId) {
    state.canonicalReplay = { commitId, nodeId, loading: true };
    state.canonicalDiff = null;
    renderInspector();
    try {
      state.canonicalReplay = await api('GET', `/books/${currentBook()}/story-graph/canonical-replay?commitId=${encodeURIComponent(commitId)}${nodeId ? `&nodeId=${encodeURIComponent(nodeId)}` : ''}&limit=120`);
      renderInspector();
    } catch (error) {
      state.canonicalReplay = { commitId, nodeId, error: error.message };
      renderInspector();
      toast(`Canonical replay 读取失败：${error.message}`, 'error');
    }
  }

  async function loadCanonicalDiff(fromCommit, toCommit, nodeId) {
    state.canonicalDiff = { fromCommit, toCommit, nodeId, loading: true };
    state.canonicalReplay = null;
    renderInspector();
    try {
      state.canonicalDiff = await api('GET', `/books/${currentBook()}/story-graph/canonical-diff?fromCommit=${encodeURIComponent(fromCommit || '')}&toCommit=${encodeURIComponent(toCommit)}${nodeId ? `&nodeId=${encodeURIComponent(nodeId)}` : ''}`);
      renderInspector();
    } catch (error) {
      state.canonicalDiff = { fromCommit, toCommit, nodeId, error: error.message };
      renderInspector();
      toast(`Canonical diff 读取失败：${error.message}`, 'error');
    }
  }

  function renderHistoryResult(inspector) {
    const history = state.history;
    if (!history || !inspector) return;
    const historyNode = nodeById(history.nodeId);
    const historyTitle = historyNode?.type === 'Chapter' ? '本章 Canon 变更 / StoryCommit' : 'History';
    if (history.loading) {
      inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section"><h4>${historyTitle}</h4><p class="dim-note">正在读取不可变 SQLite 证据…</p></div>`);
      return;
    }
    if (history.error) {
      inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section"><h4>${historyTitle}</h4><p class="dim-note">${text(history.error)}</p></div>`);
      return;
    }
    const entries = Array.isArray(history.entries) ? history.entries : [];
    const versionEntries = entries.filter((entry) => entry.kind === 'chapter_version' && entry.sourceId);
    const row = (entry) => {
      const detail = entry.version != null
        ? `v${text(entry.version)}${entry.commitStatus ? ` · ${text(entry.commitStatus)}` : ''}`
        : (entry.commitStatus || entry.kind || 'record');
      const chapter = entry.chapterNumber != null ? ` · Ch.${text(entry.chapterNumber)}` : '';
      const projectionBoundary = entry.kind === 'graph_snapshot' && entry.reason === 'story_commit_accept'
        ? '<small>captured after accepted StoryCommit</small>'
        : '';
      const projectionFailure = entry.kind === 'graph_snapshot_capture_failure'
        ? `<small class="sf-history-warning">${text(entry.error || 'The accepted commit has no graph snapshot yet.')}</small><button class="btn btn-sm btn-secondary sf-history-diff-button" data-sf-history-snapshot-retry="${attr(entry.commitId || '')}">Retry safe capture</button>`
        : '';
      const facts = Array.isArray(entry.facts) ? entry.facts : [];
      const factSummary = facts.slice(0, 3).map((fact) => {
        if (typeof fact === 'string') return fact;
        if (!fact || typeof fact !== 'object') return String(fact ?? '');
        return fact.content || fact.fact_type || fact.factType || fact.type || 'fact';
      }).filter(Boolean).join('；');
      const stateChanges = entry.stateChanges && typeof entry.stateChanges === 'object' ? entry.stateChanges : {};
      const stateSummary = Object.entries(stateChanges).slice(0, 3).map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : value}`).join('；');
      const changeSummary = [factSummary ? `事实：${factSummary}` : '', stateSummary ? `状态：${stateSummary}` : ''].filter(Boolean).join(' · ');
      const changeMarkup = changeSummary ? `<small class="sf-history-change">${text(changeSummary)}${facts.length > 3 || Object.keys(stateChanges).length > 3 ? ' …' : ''}</small>` : '';
      const compare = entry.kind === 'graph_snapshot' && entry.previousSnapshotId
        ? `<button class="btn btn-sm btn-ghost sf-history-diff-button" data-sf-history-from="${attr(entry.previousSnapshotId)}" data-sf-history-to="${attr(entry.snapshotId)}">查看此快照差异</button>`
        : '';
      const replay = entry.commitId
        ? `<button class="btn btn-sm btn-ghost sf-history-diff-button" data-sf-canonical-replay="${attr(entry.commitId)}">查看 Canon replay</button>`
        : '';
      const canonicalCompare = entry.commitId && entry.canonicalPreviousCommitId
        ? `<button class="btn btn-sm btn-ghost sf-history-diff-button" data-sf-canonical-from="${attr(entry.canonicalPreviousCommitId)}" data-sf-canonical-to="${attr(entry.commitId)}">查看 Canon diff</button>`
        : '';
      const chapterImpact = entry.kind === 'chapter_version' && entry.sourceId
        ? `<button class="btn btn-sm btn-ghost sf-history-diff-button" data-sf-history-chapter-impact="${attr(entry.sourceId)}">${state.chapterImpact?.versionId === entry.sourceId ? '\u5f53\u524d\u7f16\u8f91\u5f71\u54cd' : '\u67e5\u770b\u7f16\u8f91\u5f71\u54cd'}</button>`
        : '';
      return `<div class="sf-history-row"><div><b>${text(entry.title || entry.kind)}</b><small>${text(entry.timestamp || 'time not recorded')}${chapter}</small>${changeMarkup}${projectionBoundary}${projectionFailure}${compare}${replay}${canonicalCompare}${chapterImpact}</div><span class="sf-status-badge ${statusClass(entry.status)}">${text(detail)}</span></div>`;
    };
    const defaultFrom = versionEntries[1]?.sourceId || '';
    const defaultTo = versionEntries[0]?.sourceId || '';
    const compareFrom = state.chapterVersionCompare?.nodeId === history.nodeId
      ? state.chapterVersionCompare.fromVersionId
      : defaultFrom;
    const compareTo = state.chapterVersionCompare?.nodeId === history.nodeId
      ? state.chapterVersionCompare.toVersionId
      : defaultTo;
    const versionCompare = versionEntries.length > 1
      ? `<div class="sf-version-compare"><div class="sf-panel-title"><span>Version compare</span><small>immutable text + current impact</small></div><div class="sf-version-compare-controls"><label>From<select data-sf-version-compare-from>${versionEntries.map((entry) => `<option value="${attr(entry.sourceId)}" ${entry.sourceId === compareFrom ? 'selected' : ''}>${text(entry.title || entry.version || entry.sourceId)}</option>`).join('')}</select></label><span class="sf-version-compare-arrow">→</span><label>To<select data-sf-version-compare-to>${versionEntries.map((entry) => `<option value="${attr(entry.sourceId)}" ${entry.sourceId === compareTo ? 'selected' : ''}>${text(entry.title || entry.version || entry.sourceId)}</option>`).join('')}</select></label><button class="btn btn-sm btn-secondary" data-sf-version-compare-action>Compare</button></div></div>`
      : '';
    const note = history.meta?.canonicalReplayAvailable
      ? 'Canonical replay/diff below replays the accepted immutable ledger; observed projection snapshots remain a separate, explicitly scoped view.'
      : history.meta?.graphSnapshotCaptureFailures
        ? 'An accepted commit is missing a StoryFlow projection snapshot. Retry is allowed only when the recorded SQLite source boundary is unchanged; otherwise the UI will refuse historical backfill.'
      : history.meta?.graphSnapshotDiffAvailable
        ? 'Graph snapshot diff is available for observed StoryFlow projections; it is not a complete replay of unobserved writes.'
      : history.meta?.chapterVersionDiffAvailable
        ? 'Chapter text diff remains available through the existing Chapter Versions boundary; this view only reads durable history.'
        : 'No historical graph snapshot is fabricated; every row below comes from an existing SQLite record.';
    const canonicalGraphHistory = history.canonicalGraphHistory || {};
    const canonicalGraphEntries = Array.isArray(canonicalGraphHistory.entries)
      ? canonicalGraphHistory.entries
      : [];
    const canonicalGraphRows = canonicalGraphEntries.map((entry) => {
      const diff = entry.diffSummary || {};
      const edgeChanges = Number(diff.addedEdges || 0) + Number(diff.removedEdges || 0);
      const diffText = entry.comparisonAvailable
        ? `${Number(diff.changedNodes || 0)} changed nodes · ${edgeChanges} edge changes`
        : (entry.comparisonReason || 'No preceding accepted snapshot boundary');
      const compare = entry.comparisonAvailable && entry.previousSnapshotId
        ? `<button class="btn btn-sm btn-ghost sf-history-diff-button" data-sf-history-from="${attr(entry.previousSnapshotId)}" data-sf-history-to="${attr(entry.snapshotId)}">View accepted graph diff</button>`
        : '';
      const snapshotText = entry.snapshotAvailable
        ? `${text(entry.snapshotNodeCount || 0)} nodes · ${text(entry.snapshotEdgeCount || 0)} edges`
        : 'snapshot unavailable';
      return `<div class="sf-history-row sf-canonical-graph-history-row"><div><b>Ch.${text(entry.chapterNumber ?? '—')} · ${text(entry.chapterTitle || entry.commitId || 'accepted commit')}</b><small>${text(entry.timestamp || 'time not recorded')} · ${text(entry.commitId || '')}</small><small class="sf-history-change">${text(snapshotText)} · ${text(diffText)}</small>${entry.snapshotAvailable && entry.snapshotId ? `<small>accepted snapshot · ${text(entry.snapshotId)}</small>` : `<small class="sf-history-warning">${text(entry.comparisonReason || 'Accepted commit has no valid graph snapshot.')}</small>`}${compare}</div><span class="sf-status-badge ${statusClass(entry.snapshotAvailable ? 'CANON' : 'STALE')}">${entry.snapshotAvailable ? 'CANON GRAPH' : 'STALE GRAPH'}</span></div>`;
    }).join('');
    const canonicalGraphHistoryMarkup = canonicalGraphHistory.available || canonicalGraphHistory.meta?.acceptedCommitCount
      ? `<div class="sf-inspector-section sf-canonical-graph-history"><div class="sf-panel-title"><span>Canon Graph history</span><small>${canonicalGraphHistory.complete ? 'accepted boundaries complete' : 'partial evidence'}</small></div><div class="sf-context-banner"><b>Accepted StoryCommit graph snapshots only</b><br><small>${text(canonicalGraphHistory.meta?.snapshotCount || 0)} snapshots · ${text(canonicalGraphHistory.meta?.comparableCount || 0)} comparable transitions · mutable entity tables are not reconstructed</small></div>${canonicalGraphRows || '<p class="dim-note">No accepted graph snapshot is recorded.</p>'}${canonicalGraphHistory.warnings?.length ? `<div class="sf-edit-impact-warnings"><h5>Historical graph boundary</h5><ul>${canonicalGraphHistory.warnings.slice(0, 4).map((warning) => `<li>${text(warning)}</li>`).join('')}</ul></div>` : ''}</div>`
      : '';
    inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section sf-history-result"><h4>${historyTitle} · ${text(history.meta?.returned || 0)}</h4><div class="sf-context-banner">${text(note)}${history.meta?.truncated ? ' Results are capped.' : ''}</div>${versionCompare}${canonicalGraphHistoryMarkup}${entries.length ? entries.map(row).join('') : '<p class="dim-note">当前节点没有已记录的持久化历史。</p>'}</div>`);
    inspector.querySelectorAll('[data-sf-history-from]').forEach((button) => button.addEventListener('click', () => loadSnapshotDiff(button.dataset.sfHistoryFrom, button.dataset.sfHistoryTo, history.nodeId)));
    inspector.querySelectorAll('[data-sf-history-snapshot-retry]').forEach((button) => button.addEventListener('click', () => retryGraphSnapshot(button.dataset.sfHistorySnapshotRetry, history.nodeId)));
    inspector.querySelectorAll('[data-sf-canonical-replay]').forEach((button) => button.addEventListener('click', () => loadCanonicalReplay(button.dataset.sfCanonicalReplay, history.nodeId)));
    inspector.querySelectorAll('[data-sf-canonical-from]').forEach((button) => button.addEventListener('click', () => loadCanonicalDiff(button.dataset.sfCanonicalFrom, button.dataset.sfCanonicalTo, history.nodeId)));
    inspector.querySelectorAll('[data-sf-history-chapter-impact]').forEach((button) => button.addEventListener('click', () => loadChapterEditImpact(history.nodeId, button.dataset.sfHistoryChapterImpact || '')));
    inspector.querySelector('[data-sf-version-compare-action]')?.addEventListener('click', () => {
      const from = inspector.querySelector('[data-sf-version-compare-from]')?.value || '';
      const to = inspector.querySelector('[data-sf-version-compare-to]')?.value || '';
      if (from && to && from !== to) loadChapterVersionCompare(history.nodeId, from, to);
      else toast('Choose two different chapter versions.', 'error');
    });
    if (state.snapshotDiff && state.snapshotDiff.nodeId === history.nodeId) renderSnapshotDiffResult(inspector);
    if (state.canonicalReplay && state.canonicalReplay.nodeId === history.nodeId) renderCanonicalReplayResult(inspector);
    if (state.canonicalDiff && state.canonicalDiff.nodeId === history.nodeId) renderCanonicalDiffResult(inspector);
    if (state.chapterVersionCompare && state.chapterVersionCompare.nodeId === history.nodeId) renderChapterVersionCompareResult(inspector);
  }

  function renderSnapshotDiffResult(inspector) {
    const snapshotDiff = state.snapshotDiff;
    if (!snapshotDiff || !inspector) return;
    if (snapshotDiff.loading) {
      inspector.insertAdjacentHTML('beforeend', '<div class="sf-inspector-section"><h4>Observed projection diff</h4><p class="dim-note">Comparing two immutable StoryFlow snapshots…</p></div>');
      return;
    }
    if (snapshotDiff.error) {
      inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section"><h4>Observed projection diff</h4><p class="dim-note">${text(snapshotDiff.error)}</p></div>`);
      return;
    }
    const diff = snapshotDiff.diff || {};
    const changed = Array.isArray(diff.changedNodes) ? diff.changedNodes : [];
    const addedNodes = Array.isArray(diff.addedNodes) ? diff.addedNodes : [];
    const removedNodes = Array.isArray(diff.removedNodes) ? diff.removedNodes : [];
    const addedEdges = Array.isArray(diff.addedEdges) ? diff.addedEdges : [];
    const removedEdges = Array.isArray(diff.removedEdges) ? diff.removedEdges : [];
    const item = (value) => `<li><code>${text(value.id || value.type || value.source || 'change')}</code> ${text(value.type || value.label || '')} ${text(value.title || `${value.source || ''} → ${value.target || ''}`)}</li>`;
    inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section sf-snapshot-diff"><h4>Observed projection diff</h4><div class="sf-context-banner">${text(snapshotDiff.scope || 'observed_projection')} · ${snapshotDiff.replayComplete ? 'replay complete' : 'not a canonical replay'}<br><small>${text(snapshotDiff.from?.id || '')} → ${text(snapshotDiff.to?.id || '')}</small></div><div class="sf-diff-summary"><span><b>${text(addedNodes.length)}</b> added nodes</span><span><b>${text(removedNodes.length)}</b> removed nodes</span><span><b>${text(changed.length)}</b> changed nodes</span><span><b>${text(addedEdges.length)}</b> added edges</span><span><b>${text(removedEdges.length)}</b> removed edges</span></div>${changed.length ? `<h5>Changed nodes</h5><ul class="sf-inspector-list">${changed.map((entry) => `<li><code>${text(entry.id)}</code> ${text(entry.before?.status || '')} → ${text(entry.after?.status || '')} · ${text(entry.after?.title || entry.before?.title || '')}</li>`).join('')}</ul>` : ''}${addedNodes.length ? `<h5>Added nodes</h5><ul class="sf-inspector-list">${addedNodes.map(item).join('')}</ul>` : ''}${removedNodes.length ? `<h5>Removed nodes</h5><ul class="sf-inspector-list">${removedNodes.map(item).join('')}</ul>` : ''}${addedEdges.length ? `<h5>Added semantic edges</h5><ul class="sf-inspector-list">${addedEdges.map(item).join('')}</ul>` : ''}${removedEdges.length ? `<h5>Removed semantic edges</h5><ul class="sf-inspector-list">${removedEdges.map(item).join('')}</ul>` : ''}${!diff.hasRelevantChange ? '<p class="dim-note">这两个快照在当前节点范围内没有可见投影变化。</p>' : ''}</div>`);
  }

  function renderCanonicalReplayResult(inspector) {
    const replay = state.canonicalReplay;
    if (!replay || !inspector) return;
    if (replay.loading) {
      inspector.insertAdjacentHTML('beforeend', '<div class="sf-inspector-section"><h4>Canonical replay</h4><p class="dim-note">按 accepted StoryCommit 顺序重放 Canon…</p></div>');
      return;
    }
    if (replay.error) {
      inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section"><h4>Canonical replay</h4><p class="dim-note">${text(replay.error)}</p></div>`);
      return;
    }
    const commits = Array.isArray(replay.commits) ? replay.commits : [];
    const facts = Array.isArray(replay.facts) ? replay.facts : [];
    const stateEntries = Object.entries(replay.state || {}).slice(0, 24);
    const target = replay.target || {};
    const historicalGraph = replay.historicalGraph || {};
    const historicalGraphLabel = historicalGraph.complete
      ? `accepted graph snapshot · ${text(historicalGraph.snapshotId || 'complete')}`
      : `graph snapshot unavailable · ${text(historicalGraph.reason || 'ledger-only')}`;
    inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section sf-canonical-replay"><h4>Canonical replay</h4><div class="sf-context-banner">${text(replay.scope || 'canonical_commits')} · accepted ledger replay complete<br><small>截至 ${text(target.commitId || 'initial state')} · ${text(replay.meta?.replayBasis || replay.replayBasis || '')}</small></div><div class="sf-context-banner ${historicalGraph.complete ? '' : 'sf-context-excluded'}">${historicalGraphLabel}<br><small>${historicalGraph.complete ? `${text(historicalGraph.nodes?.length || 0)} focused nodes · ${text(historicalGraph.edges?.length || 0)} semantic edges` : 'No historical entity graph is inferred from current tables.'}</small></div><dl class="sf-kv"><dt>Commit 数</dt><dd>${text(replay.meta?.replayedCommitCount || commits.length)}</dd><dt>Fact 数</dt><dd>${text(replay.meta?.returnedFactCount || facts.length)}</dd><dt>State keys</dt><dd>${text(stateEntries.length)}</dd><dt>Graph replay</dt><dd>${historicalGraph.complete ? 'complete' : 'ledger only'}</dd></dl>${stateEntries.length ? `<h5>State after boundary</h5><ul class="sf-inspector-list">${stateEntries.map(([key, value]) => `<li><code>${text(key)}</code> ${text(typeof value === 'object' ? JSON.stringify(value) : value)}</li>`).join('')}</ul>` : ''}${facts.length ? `<h5>Canonical facts</h5><ul class="sf-inspector-list">${facts.slice(0, 12).map((fact) => `<li><b>${text(fact.factType || 'fact')}</b> ${text(fact.content)}<small class="sf-analysis-evidence">${text(fact.commitId || '')}</small></li>`).join('')}</ul>` : ''}<p class="dim-note">Canonical facts/state always come from the immutable ledger. Entity nodes and edges are shown only when the accepted commit snapshot exists.</p></div>`);
  }

  function renderCanonicalDiffResult(inspector) {
    const diff = state.canonicalDiff;
    if (!diff || !inspector) return;
    if (diff.loading) {
      inspector.insertAdjacentHTML('beforeend', '<div class="sf-inspector-section"><h4>Canonical diff</h4><p class="dim-note">比较两个 accepted Canon commit boundary…</p></div>');
      return;
    }
    if (diff.error) {
      inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section"><h4>Canonical diff</h4><p class="dim-note">${text(diff.error)}</p></div>`);
      return;
    }
    const addedCommits = Array.isArray(diff.addedCommits) ? diff.addedCommits : [];
    const addedFacts = Array.isArray(diff.addedFacts) ? diff.addedFacts : [];
    const removedFacts = Array.isArray(diff.removedFacts) ? diff.removedFacts : [];
    const changedState = Array.isArray(diff.changedState) ? diff.changedState : [];
    const historicalGraph = diff.historicalGraph || {};
    const historicalGraphDiff = historicalGraph.diff || {};
    const historicalChangedNodes = Array.isArray(historicalGraphDiff.changedNodes) ? historicalGraphDiff.changedNodes : [];
    const historicalAddedEdges = Array.isArray(historicalGraphDiff.addedEdges) ? historicalGraphDiff.addedEdges : [];
    const historicalRemovedEdges = Array.isArray(historicalGraphDiff.removedEdges) ? historicalGraphDiff.removedEdges : [];
    inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section sf-canonical-replay"><h4>Canonical diff</h4><div class="sf-context-banner">${text(diff.scope || 'canonical_commits')} · accepted ledger replay complete<br><small>${text(diff.from?.commitId || 'initial')} → ${text(diff.to?.commitId || '')}</small></div><div class="sf-context-banner ${historicalGraph.complete ? '' : 'sf-context-excluded'}">${historicalGraph.complete ? 'accepted graph snapshot diff · complete' : `graph snapshot diff unavailable · ${text(historicalGraph.reason || 'ledger-only')}`}<br><small>${historicalGraph.complete ? `${text(historicalChangedNodes.length)} changed nodes · ${text(historicalAddedEdges.length + historicalRemovedEdges.length)} edge changes` : 'No historical entity graph is inferred from current tables.'}</small></div><div class="sf-diff-summary"><span><b>${text(addedCommits.length)}</b> commits</span><span><b>${text(addedFacts.length)}</b> facts</span><span><b>${text(changedState.length)}</b> state changes</span><span><b>${text(removedFacts.length)}</b> removed facts</span><span><b>${historicalChangedNodes.length}</b> graph nodes</span></div>${changedState.length ? `<h5>StoryState changes</h5><ul class="sf-inspector-list">${changedState.map((item) => `<li><code>${text(item.key)}</code> ${text(item.before)} → ${text(item.after)}</li>`).join('')}</ul>` : ''}${addedFacts.length ? `<h5>Added Canon facts</h5><ul class="sf-inspector-list">${addedFacts.slice(0, 12).map((fact) => `<li>${text(fact.content)}<small class="sf-analysis-evidence">${text(fact.commitId || '')}</small></li>`).join('')}</ul>` : ''}<p class="dim-note">这是 accepted StoryCommit / StoryFact / StoryState 与 accepted graph snapshot 的确定性差异；缺失快照时不会伪造实体历史。</p></div>`);
  }

  function renderImpactResult(inspector) {
    const impact = state.impact;
    if (!impact || !inspector) return;
    if (impact.loading) {
      inspector.insertAdjacentHTML('beforeend', '<div class="sf-inspector-section"><h4>影响分析</h4><p class="dim-note">沿语义出边读取下游影响…</p></div>');
      return;
    }
    if (impact.error) {
      inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section"><h4>影响分析</h4><p class="dim-note">${text(impact.error)}</p></div>`);
      return;
    }
    const boundaryLabel = {
      CANON: 'CANON · 已发生',
      ACCEPTED: 'ACCEPTED · 已接受',
      PLANNED: 'PLANNED · 规划',
      CANDIDATE: 'CANDIDATE · 候选',
      DRAFT: 'DRAFT · 草稿',
      SUPERSEDED: 'SUPERSEDED · 已替代',
      STALE: 'STALE · 过期',
      CONFLICT: 'CONFLICT · 冲突',
      PROJECTION: 'PROJECTION · 投影',
    };
    const evidenceLabel = {
      story_fact: 'StoryFact',
      story_commit: 'StoryCommit',
      story_state: 'StoryState',
      plot_workspace: 'Planning',
      sqlite_source: 'SQLite source',
    };
    const item = (entry) => {
      const boundary = String(entry.impactBoundary || entry.node?.status || 'PROJECTION').toUpperCase();
      const evidence = Array.isArray(entry.evidence) ? entry.evidence.slice(0, 4) : [];
      const evidenceText = entry.evidenceStatus === 'recorded' && evidence.length
        ? evidence.map((source) => `${evidenceLabel[source.kind] || source.kind} · ${source.id}`).join(' · ')
        : '仅有节点投影；没有可核验的事实来源';
      return `<div class="sf-impact-item"><div class="sf-neighbor-row"><span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><b>${text(nodeLabel(entry.node?.type))}</b> · ${text(entry.node?.title)}<br><small style="color:var(--text-muted)">${text(entry.reason)}</small></span><span class="sf-neighbor-edge">${text(entry.edge?.type)} · D${text(entry.depth)}</span></div><div class="sf-impact-evidence"><span class="sf-status-badge ${statusClass(boundary)}">${text(boundaryLabel[boundary] || boundary)}</span><small>${text(evidenceText)}</small></div></div>`;
    };
    const direct = Array.isArray(impact.direct) ? impact.direct : [];
    const downstream = Array.isArray(impact.downstream) ? impact.downstream : [];
    const boundarySummary = Object.entries(impact.meta?.boundaryCounts || {})
      .map(([boundary, count]) => `${boundaryLabel[boundary] || boundary}: ${count}`)
      .join(' · ');
    const evidenceSummary = Object.entries(impact.meta?.evidenceStatusCounts || {})
      .map(([status, count]) => `${status === 'recorded' ? '有来源' : '仅投影'} ${count}`)
      .join(' · ');
    inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section sf-impact-result"><h4>影响分析 · ${text(impact.meta?.returned || 0)} 个节点</h4><div class="sf-context-banner">${impact.meta?.conflictOrStaleCount ? `发现 ${text(impact.meta.conflictOrStaleCount)} 个 STALE / CONFLICT 下游节点。` : '当前投影没有发现 STALE / CONFLICT 下游节点。'} ${impact.meta?.truncated ? '结果已按上限截断。' : ''}<br><small>${text(boundarySummary || '未记录边界')} · ${text(evidenceSummary || '未记录来源')}</small></div>${direct.length ? `<h5>直接影响</h5>${direct.map(item).join('')}` : '<p class="dim-note">没有直接语义出边。</p>'}${downstream.length ? `<h5>下游影响</h5>${downstream.map(item).join('')}` : ''}<p class="dim-note">来源：SQLite Story Graph；每项 evidence 只展示已记录的 StoryFact / StoryCommit / StoryState / Planning 来源，不会从布局或文本推断影响。</p></div>`);
  }

  function renderChapterEditImpactResult(inspector) {
    const report = state.chapterImpact;
    if (!report || !inspector) return;
    if (report.loading) {
      inspector.insertAdjacentHTML('beforeend', '<div class="sf-inspector-section"><h4>章节编辑影响</h4><p class="dim-note">读取 ChapterVersion、StoryCommit、StoryState 与记录的语义下游依赖…</p></div>');
      return;
    }
    if (report.error) {
      inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section"><h4>章节编辑影响</h4><p class="dim-note">${text(report.error)}</p></div>`);
      return;
    }
    const version = report.version || {};
    const canonical = report.canonical || {};
    const storyState = report.state || {};
    const meta = report.meta || {};
    const warnings = Array.isArray(report.warnings) ? report.warnings : [];
    const future = Array.isArray(report.futureChapters) ? report.futureChapters : [];
    const facts = Array.isArray(report.affectedFacts) ? report.affectedFacts : [];
    const planning = Array.isArray(report.planningDependencies) ? report.planningDependencies : [];
    const hazards = Array.isArray(report.hazards) ? report.hazards : [];
    const renderImpactRows = (items) => items.slice(0, 16).map((entry) => {
      const node = entry.node || {};
      const evidence = Array.isArray(entry.evidence) ? entry.evidence.slice(0, 2) : [];
      const source = evidence.length
        ? evidence.map((item) => `${item.kind || 'source'} · ${item.id || '—'}`).join(' · ')
        : '仅有节点投影；没有可核验来源';
      return `<li class="sf-edit-impact-row"><button data-sf-neighbor="${attr(node.id || '')}">${text(nodeLabel(node.type || 'Node'))} · ${text(node.title || node.id || '—')}</button><span>${text(entry.edge?.type || 'semantic')} · D${text(entry.depth || '—')}</span><small>${text(source)}</small></li>`;
    }).join('');
    const stateLabel = storyState.stale ? 'STALE · 需要重新提取' : '当前状态未标记 stale';
    inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section sf-chapter-edit-impact"><h4>章节编辑影响</h4><div class="sf-context-banner ${storyState.stale || hazards.length ? 'sf-context-excluded' : ''}"><b>只读依赖报告</b> · 不会修改 StoryFact / StoryState / StoryCommit<br><small>${text(meta.analysisKind || 'recorded_dependency_surface')} · ${text(meta.dependencyEvidence || 'recorded SQLite sources only')}</small></div><dl class="sf-kv"><dt>版本</dt><dd>${text(version.version ?? '未记录')} · ${text(version.id || '无 ChapterVersion')}</dd><dt>最近 Canon Commit</dt><dd>${text(canonical.commitId || '未记录')} · ${text(canonical.status || '—')}</dd><dt>StoryState</dt><dd>${text(stateLabel)} · v${text(storyState.stateVersion ?? '—')}</dd><dt>后续章节</dt><dd>${text(meta.futureChapterCount ?? future.length)}</dd><dt>受影响事实</dt><dd>${text(meta.affectedFactCount ?? facts.length)}</dd><dt>规划依赖</dt><dd>${text(meta.planningDependencyCount ?? planning.length)}</dd></dl>${warnings.length ? `<div class="sf-edit-impact-warnings"><h5>边界与警告</h5><ul>${warnings.slice(0, 8).map((warning) => `<li>${text(warning)}</li>`).join('')}</ul></div>` : ''}${future.length ? `<h5>后续章节依赖</h5><ul class="sf-inspector-list sf-edit-impact-list">${renderImpactRows(future)}</ul>` : '<p class="dim-note">没有找到已记录的后续章节语义依赖。</p>'}${facts.length ? `<h5>事实与状态依赖</h5><ul class="sf-inspector-list sf-edit-impact-list">${renderImpactRows(facts)}</ul>` : ''}${planning.length ? `<h5>规划覆盖</h5><ul class="sf-inspector-list sf-edit-impact-list">${renderImpactRows(planning)}</ul>` : ''}${hazards.length ? `<h5>风险节点</h5><ul class="sf-inspector-list sf-edit-impact-list">${renderImpactRows(hazards)}</ul>` : ''}<p class="dim-note">该报告只解释已有 ChapterVersion、StoryCommit、StoryState 和语义边；不会从正文相似度、布局或普通连线推断未来 Canon。</p></div>`);
  }

  function renderChapterVersionCompareResult(inspector) {
    const comparison = state.chapterVersionCompare;
    if (!comparison || !inspector) return;
    if (comparison.loading) {
      inspector.insertAdjacentHTML('beforeend', '<div class="sf-inspector-section"><h4>Version compare</h4><p class="dim-note">Comparing immutable ChapterVersion records and the current Story Graph surface…</p></div>');
      return;
    }
    if (comparison.error) {
      inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section"><h4>Version compare</h4><p class="dim-note">${text(comparison.error)}</p></div>`);
      return;
    }
    const from = comparison.from || {};
    const to = comparison.to || {};
    const diff = comparison.textDiff || {};
    const surface = comparison.dependencySurface || {};
    const future = Array.isArray(surface.futureChapters) ? surface.futureChapters : [];
    const facts = Array.isArray(surface.affectedFacts) ? surface.affectedFacts : [];
    const planning = Array.isArray(surface.planningDependencies) ? surface.planningDependencies : [];
    const hazards = Array.isArray(surface.hazards) ? surface.hazards : [];
    const canonical = comparison.canonicalSurface || {};
    const canonicalAddedFacts = Array.isArray(canonical.addedFacts) ? canonical.addedFacts : [];
    const canonicalRemovedFacts = Array.isArray(canonical.removedFacts) ? canonical.removedFacts : [];
    const canonicalState = Array.isArray(canonical.changedState) ? canonical.changedState : [];
    const canonicalCommit = (record) => record?.commit || {};
    const canonicalFactRow = (fact, prefix) => `<li><b>${text(prefix)}</b> ${text(fact.content || fact.factType || 'fact')}<small class="sf-analysis-evidence">${text(fact.commitId || '')}</small></li>`;
    const canonicalWarnings = Array.isArray(canonical.warnings) ? canonical.warnings : [];
    const historicalGraph = canonical.historicalGraph || {};
    const historicalGraphDiff = historicalGraph.diff || {};
    const historicalChangedNodes = Array.isArray(historicalGraphDiff.changedNodes) ? historicalGraphDiff.changedNodes : [];
    const historicalAddedNodes = Array.isArray(historicalGraphDiff.addedNodes) ? historicalGraphDiff.addedNodes : [];
    const historicalRemovedNodes = Array.isArray(historicalGraphDiff.removedNodes) ? historicalGraphDiff.removedNodes : [];
    const historicalAddedEdges = Array.isArray(historicalGraphDiff.addedEdges) ? historicalGraphDiff.addedEdges : [];
    const historicalRemovedEdges = Array.isArray(historicalGraphDiff.removedEdges) ? historicalGraphDiff.removedEdges : [];
    const historicalDependency = canonical.historicalDependencySurface || {};
    const historicalDependencyFuture = Array.isArray(historicalDependency.futureChapters) ? historicalDependency.futureChapters : [];
    const historicalDependencyDirect = Array.isArray(historicalDependency.direct) ? historicalDependency.direct : [];
    const historicalDependencyDownstream = Array.isArray(historicalDependency.downstream) ? historicalDependency.downstream : [];
    const historicalGraphSection = historicalGraph.complete
      ? `<div class="sf-context-banner"><b>Historical graph snapshot · complete</b><br><small>${text(historicalGraph.scope || 'accepted_commit_snapshot_diff')} · ${text(historicalGraph.from?.snapshotId || '—')} → ${text(historicalGraph.to?.snapshotId || '—')}</small></div><div class="sf-diff-summary"><span><b>${text(historicalAddedNodes.length)}</b> added nodes</span><span><b>${text(historicalRemovedNodes.length)}</b> removed nodes</span><span><b>${text(historicalChangedNodes.length)}</b> changed nodes</span><span><b>${text(historicalAddedEdges.length + historicalRemovedEdges.length)}</b> edge changes</span></div>`
      : `<div class="sf-context-banner sf-context-excluded"><b>Historical graph snapshot · unavailable</b><br><small>${text(historicalGraph.reason || 'No accepted projection snapshot covers both boundaries.')}</small></div>`;
    const canonicalSection = canonical.available
      ? `<div class="sf-version-ledger"><div class="sf-panel-title"><span>Canonical commit evidence</span><small>${canonical.stateComplete ? 'state projections recorded' : 'state projection incomplete'}</small></div><div class="sf-context-banner"><b>${text(canonicalCommit(canonical.from).status || 'missing')} → ${text(canonicalCommit(canonical.to).status || 'missing')}</b><br><small>${text(canonical.scope || 'canonical_commit_projection')} · ${text(canonical.factEvidence || 'SQLite facts')} · ${text(canonical.stateEvidence || 'SQLite state')}</small></div><div class="sf-diff-summary"><span><b>${text(canonicalAddedFacts.length)}</b> added facts</span><span><b>${text(canonicalRemovedFacts.length)}</b> removed facts</span><span><b>${text(canonicalState.length)}</b> state changes</span><span><b>${canonical.commitEvidenceComplete ? 'complete' : 'partial'}</b> commit boundary</span></div>${historicalGraphSection}${canonicalState.length ? `<h5>StoryState at commit boundary</h5><ul class="sf-inspector-list">${canonicalState.slice(0, 16).map((item) => `<li><code>${text(item.key)}</code> ${text(typeof item.before === 'object' ? JSON.stringify(item.before) : item.before)} → ${text(typeof item.after === 'object' ? JSON.stringify(item.after) : item.after)}</li>`).join('')}</ul>` : ''}${canonicalAddedFacts.length ? `<h5>Facts added by target commit</h5><ul class="sf-inspector-list">${canonicalAddedFacts.slice(0, 12).map((fact) => canonicalFactRow(fact, '+')).join('')}</ul>` : ''}${canonicalRemovedFacts.length ? `<h5>Facts removed from source boundary</h5><ul class="sf-inspector-list">${canonicalRemovedFacts.slice(0, 12).map((fact) => canonicalFactRow(fact, '-')).join('')}</ul>` : ''}${canonicalWarnings.length ? `<div class="sf-edit-impact-warnings"><h5>Canonical evidence boundary</h5><ul>${canonicalWarnings.slice(0, 4).map((item) => `<li>${text(item)}</li>`).join('')}</ul></div>` : ''}<p class="dim-note">The graph section uses accepted commit projection snapshots when available. Mutable tables are never presented as historical state without that snapshot evidence.</p></div>`
      : '<div class="sf-version-ledger"><div class="sf-panel-title"><span>Canonical commit evidence</span><small>not available</small></div><p class="dim-note">Neither version has enough persisted StoryCommit evidence to build a canonical boundary comparison.</p></div>';
    const surfaceRow = (entry) => {
      const node = entry.node || {};
      return `<li class="sf-edit-impact-row"><button data-sf-neighbor="${attr(node.id || '')}">${text(nodeLabel(node.type || 'Node'))} · ${text(node.title || node.id || '—')}</button><span>${text(entry.edge?.type || 'semantic')} · D${text(entry.depth || '—')}</span><small>${text(entry.reason || entry.evidenceStatus || 'recorded projection')}</small></li>`;
    };
    const section = (title, items) => items.length
      ? `<h5>${title}</h5><ul class="sf-inspector-list sf-edit-impact-list">${items.slice(0, 16).map(surfaceRow).join('')}</ul>`
      : '';
    const historicalDependencySection = historicalDependency.complete
      ? `<div class="sf-version-ledger sf-historical-dependency"><div class="sf-panel-title"><span>Historical dependency surface</span><small>accepted snapshots</small></div><div class="sf-context-banner"><b>Recorded downstream dependencies</b><br><small>${text(historicalDependency.scope || 'accepted_commit_snapshot_dependency_surface')} · ${text(historicalDependency.meta?.seedNodeCount || 0)} seed nodes · ${text(historicalDependency.meta?.returned || 0)} traversed nodes</small></div><div class="sf-diff-summary"><span><b>${text(historicalDependency.changedNodeIds?.length || 0)}</b> changed nodes</span><span><b>${text(historicalDependency.changedEdgeIds?.length || 0)}</b> changed edges</span><span><b>${text(historicalDependencyFuture.length)}</b> future chapters</span><span><b>${text(historicalDependency.meta?.depth || 0)}</b> depth</span></div>${section('Historical future chapters', historicalDependencyFuture)}${section('Historical direct dependencies', historicalDependencyDirect)}${section('Historical downstream dependencies', historicalDependencyDownstream)}<p class="dim-note">Evidence comes from the two accepted StoryCommit graph snapshots and target semantic edges; it is not a prose-causality prediction.</p></div>`
      : `<div class="sf-version-ledger sf-historical-dependency"><div class="sf-panel-title"><span>Historical dependency surface</span><small>unavailable</small></div><p class="dim-note">${text(historicalDependency.reason || 'Both accepted graph snapshots are required for historical dependency traversal.')}</p></div>`;
    const warning = (comparison.warnings || []).slice(0, 4).map((item) => `<li>${text(item)}</li>`).join('');
    inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section sf-version-compare-result"><h4>Version compare</h4><div class="sf-context-banner"><b>${text(from.title || `v${from.version || '—'}`)} → ${text(to.title || `v${to.version || '—'}`)}</b><br><small>${text(comparison.scope || 'chapter_version_comparison')} · ${text(comparison.canonicalSource || 'sqlite')} · read-only</small></div><div class="sf-diff-summary"><span><b>${text(diff.addedLines || 0)}</b> added lines</span><span><b>${text(diff.removedLines || 0)}</b> removed lines</span><span><b>${diff.changed ? 'changed' : 'unchanged'}</b> text</span><span><b>${text(future.length)}</b> future chapters</span><span><b>${text(facts.length)}</b> facts</span></div>${diff.unifiedDiff ? `<details class="sf-version-diff" open><summary>Text diff${diff.truncated ? ' · truncated' : ''}</summary><pre>${text(diff.unifiedDiff)}</pre></details>` : '<p class="dim-note">The two immutable versions have no text difference.</p>'}${canonicalSection}${historicalDependencySection}${warning ? `<div class="sf-edit-impact-warnings"><h5>Evidence boundary</h5><ul>${warning}</ul></div>` : ''}<p class="dim-note">Dependency surface: ${text(surface.scope || 'current_projection')}. The historical section is shown only when both accepted graph snapshots exist.</p>${section('Future chapter dependencies', future)}${section('Affected facts', facts)}${section('Planning dependencies', planning)}${section('Hazards', hazards)}</div>`);
  }

  async function loadContext(chapterId, generationRunId = '', depth = state?.depth || 1) {
    state.contextLoading = true;
    renderInspector();
    try {
      const params = new URLSearchParams({ depth: String(Math.max(1, Math.min(Number(depth) || 1, 3))) });
      if (generationRunId) params.set('generation_run_id', generationRunId);
      const runQuery = `?${params.toString()}`;
      state.context = await api('GET', `/books/${currentBook()}/story-graph/context/${encodeURIComponent(chapterId)}${runQuery}`);
      state.contextChapterId = state.context.chapterId || chapterId;
      state.contextDepth = Number(state.context.graph?.meta?.contextDepth || state.context.graph?.meta?.depth || depth || 1);
      state.contextError = null;
      state.contextEvidence = null;
      state.view = 'context';
      state.focus = state.contextChapterId;
      state.selected = new Set([state.contextChapterId]);
      state.edgeSelectedId = null;
           state.detail = null;
           state.impact = null;
           state.chapterImpact = null;
           state.chapterVersionCompare = null;
           state.history = null;
      state.canonicalReplay = null;
      state.canonicalDiff = null;
      state.graph = state.context.graph || { nodes: [], edges: [], meta: {} };
      rememberGraphSnapshot(state.graph);
      renderToolbar();
      renderSidebar();
      renderCanvas();
      renderInspector();
      renderContextInspector();
      window.requestAnimationFrame(() => {
        const chapter = nodeById(state.contextChapterId);
        if (chapter) centerOn(chapter);
        else fitGraph();
      });
    } catch (error) {
      state.contextError = error.message;
      renderInspector();
      toast(`Context 读取失败：${error.message}`, 'error');
    } finally {
      state.contextLoading = false;
    }
  }

  function focusContextEvidence(nodeId) {
    const source = (state.context?.sources || []).find((item) => item.nodeId === nodeId);
    state.contextEvidence = source || null;
    state.edgeSelectedId = null;
    state.focus = nodeId;
    state.selected = new Set([nodeId]);
    renderCanvas();
    renderSidebar();
    renderInspector();
    const node = nodeById(nodeId);
    if (node) {
      centerOn(node);
      if (node.type !== 'ContextSource') loadNodeDetail(nodeId);
    }
  }

  function renderContextInspector() {
    const inspector = document.getElementById('sf-inspector');
    if (!inspector || !state.context) return;
    const context = state.context;
    const sources = Array.isArray(context.sources) ? context.sources : [];
    const tokenSummary = context.tokenSummary || {};
    const breakdown = Array.isArray(tokenSummary.breakdown) ? tokenSummary.breakdown : [];
    const sections = Array.isArray(tokenSummary.contextSections) ? tokenSummary.contextSections : [];
    const components = Array.isArray(tokenSummary.componentAttribution)
      ? tokenSummary.componentAttribution
      : (Array.isArray(tokenSummary.promptComponents) ? tokenSummary.promptComponents : []);
    const graphSnapshot = context.trace?.contextGraphSnapshot || tokenSummary.contextGraphSnapshot || {};
    const sourceAvailability = tokenSummary.sourceAvailability || context.trace?.manifest?.availability || {};
    const availabilityRows = Object.entries(sourceAvailability).map(([sourceType, availability]) => {
      const record = availability && typeof availability === 'object' ? availability : {};
      const status = String(record.status || 'not_recorded');
      const detail = record.reason || (status === 'included' ? 'recorded in Writer manifest' : 'not recorded');
      return `<div class="sf-context-breakdown-row"><span>${text(sourceType)}</span><b>${text(status)}</b><small>${text(detail)}</small></div>`;
    }).join('');
    const runs = Array.isArray(context.trace?.availableRuns) ? context.trace.availableRuns : [];
    const graphMeta = context.graph?.meta || {};
    const sourceRows = sources.slice(0, 80).map((source) => {
      const included = source.included !== false;
      const reason = included ? (source.inclusionReason || source.reason || 'included') : (source.excludedReason || source.reason || 'excluded');
      return `<button class="sf-neighbor-row sf-context-source-button" data-sf-context-source="${attr(source.nodeId || '')}" ${source.nodeId ? '' : 'disabled'}><span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><b>${text(nodeLabel(source.type))}</b> · ${text(source.title)}<br><small style="color:var(--text-muted)">${text(reason)}${source.selectionRole ? ` · ${text(source.selectionRole)}` : ''}${source.contextSectionTitle ? ` · ${text(source.contextSectionTitle)}` : ''}</small></span><span class="sf-neighbor-edge">${text(included ? 'INCLUDED' : 'EXCLUDED')}</span></button>`;
    }).join('');
    const sectionRows = sections.map((section) => `<div class="sf-context-breakdown-row"><span>${text(section.title)}</span><b>${text(section.contentChars)} chars</b><small>${text(section.binding || 'manifest')} · ${text((section.sourceTypes || []).join(', ') || 'no source type')}</small></div>`).join('');
    const componentRows = components.map((component) => {
      const range = component.persistedPromptRange || component.promptRange;
      const rangeText = range ? ` · ${text(range.scope || 'prompt')} ${text(range.start)}–${text(range.end)}` : '';
      const tokenText = component.estimatedTokens != null
        ? `≈ ${text(component.estimatedTokens)} tokens`
        : 'token estimate unavailable';
      return `<div class="sf-context-breakdown-row"><span>${text(component.label || component.id)}</span><b>${text(component.contentChars ?? '—')} chars</b><small>${tokenText} · ${text(component.binding || 'prompt component')} · ${text(component.location || '—')}${rangeText}</small></div>`;
    }).join('');
    const tokenAttribution = tokenSummary.tokenAttribution || {};
    const tokenAttributionBanner = `<div class="sf-context-banner"><b>Token provenance · ${text(tokenAttribution.status || 'not recorded')}</b><br><small>Provider usage scope: ${text(tokenAttribution.providerUsageScope || 'not recorded')} · per-source provider offsets: ${tokenAttribution.exactPerSourceProviderTokens ? 'recorded' : 'not recorded'} · source estimate basis: ${text(tokenAttribution.sourceEstimateBasis || 'unavailable')}</small></div>`;
    const inputAccounting = tokenSummary.inputAccounting || {};
    const accountingMetric = (label, value, suffix = '') => `<div><b>${text(value ?? '—')}${suffix}</b><span>${text(label)}</span></div>`;
    const inputAccountingBlock = `<div class="sf-inspector-section sf-context-accounting"><h4>Input accounting · character-level</h4><div class="sf-context-banner ${inputAccounting.status === 'exact_character_accounting' ? '' : 'sf-context-excluded'}"><b>${text(inputAccounting.status || 'unavailable')}</b><br><small>${text(inputAccounting.reason || '没有可用的 persisted prompt accounting。')}</small></div><div class="sf-selection-summary-grid">${accountingMetric('persisted prompt', inputAccounting.promptChars, ' chars')}${accountingMetric('manifest union', inputAccounting.uniqueCoveredChars, ' chars')}${accountingMetric('untracked message', inputAccounting.untrackedMessageChars, ' chars')}${accountingMetric('range overlap', inputAccounting.overlapChars, ' chars')}</div><dl class="sf-kv"><dt>Coverage</dt><dd>${text(inputAccounting.coveragePercent != null ? `${inputAccounting.coveragePercent}% of persisted input` : 'unavailable')}</dd><dt>Writer message</dt><dd>${text(inputAccounting.messageChars != null ? `${inputAccounting.coveredMessageChars || 0} / ${inputAccounting.messageChars} chars tracked` : 'segment boundary unavailable')}</dd><dt>Recorded ranges</dt><dd>${text(inputAccounting.recordedRangeCount ?? 0)} · ${text(inputAccounting.includedSourceWithoutPersistedRange ?? 0)} included sources without a persisted range</dd><dt>Provider token offsets</dt><dd>${inputAccounting.providerTokenOffsets ? 'recorded' : 'not recorded'}</dd></dl><p class="dim-note">覆盖值是 source / section / prompt-component 范围的 union；overlap 是层级 provenance 的重复覆盖，不是额外 prompt。字符计量不能冒充 provider token 计量。</p></div>`;
    const runPicker = runs.length
      ? `<div class="sf-context-run-picker"><label for="sf-context-run">Writer GenerationRun</label><select id="sf-context-run" data-sf-context-run>${runs.map((run) => {
        const selected = String(run.id || '') === String(context.trace?.selectedRunId || context.trace?.generationRunId || '') ? ' selected' : '';
        const manifest = run.hasContextManifest ? ' · manifest' : ' · no manifest';
        return `<option value="${attr(run.id || '')}"${selected}>${text(run.id || 'run')} · ${text(run.status || 'unknown')}${manifest}</option>`;
      }).join('')}</select><small>${text(runs.length)} 个 Writer runs 可从 SQLite 选择；不会从提示词反推缺失 provenance。</small></div>`
      : '<p class="dim-note">本章没有可选择的 Writer GenerationRun。</p>';
    const graphSnapshotBanner = graphSnapshot.available
      ? `<div class="sf-context-banner ${graphSnapshot.valid ? '' : 'sf-context-excluded'}"><b>Context Graph snapshot · ${graphSnapshot.valid ? 'integrity verified' : 'integrity unavailable'}</b><br><small>${text(graphSnapshot.nodeCount || 0)} source/focus nodes · ${text(graphSnapshot.edgeCount || 0)} inclusion/selection edges · ${text(graphSnapshot.graphSha256 || 'no hash')}</small><br><small>${text(graphSnapshot.integrityReason || 'snapshot is embedded in the persisted GenerationRun manifest')}</small></div>`
      : '<div class="sf-context-banner sf-context-excluded"><b>Context Graph snapshot · not captured</b><br><small>该旧 GenerationRun 只有 source manifest；不会从当前图谱反推历史 Writer 输入。</small></div>';
    inspector.innerHTML = planningEditBanner() + `<div class="sf-inspector-head"><div><h3>Chapter Context</h3><p>${text(context.chapterId)}</p></div><span class="sf-status-badge status-candidate">TRACE · D${text(context.graph?.meta?.contextDepth || context.graph?.meta?.depth || state.contextDepth || 1)}</span></div><div class="sf-context-banner">${context.trace?.available ? '这是 GenerationRun 持久化的实际上下文清单。' : text(context.trace?.reason || '当前没有持久化的 Writer token manifest；下面只显示可追溯的候选上下文。')}</div>${graphMeta.contextGraph ? `<div class="sf-context-banner">Canvas edges are the persisted GenerationRun manifest: ${text(graphMeta.contextIncludedSources || 0)} included, ${text(graphMeta.contextExcludedSources || 0)} excluded. Context nodes are read-only evidence. Graph depth ${text(graphMeta.contextDepth || graphMeta.depth || state.contextDepth || 1)} is an explicit bounded projection; it does not change the recorded Writer input.</div>` : ''}${graphSnapshotBanner}${inputAccountingBlock}${context.excludedSources?.length ? `<div class="sf-context-banner sf-context-excluded">${text(context.excludedSources.length)} 个来源被记录但未纳入 Writer 输入；排除原因保持来自 manifest，不从图中推断。</div>` : ''}<div class="sf-inspector-section"><h4>${context.trace?.available ? '实际来源' : '候选来源'} ${sources.length}</h4><div>${sourceRows || '<p class="dim-note">没有候选上下文。</p>'}</div></div><div class="sf-inspector-section"><h4>上下文构成</h4>${breakdown.length ? `<div class="sf-context-breakdown">${breakdown.map((item) => `<div class="sf-context-breakdown-row"><span>${text(item.sourceType)}</span><b>${text(item.contentChars)} chars</b><small>≈ ${text(item.estimatedTokens)} tokens · ${text(item.includedItems)}/${text(item.items)} included</small></div>`).join('')}</div><p class="dim-note">分项 token 是 contentChars/4 的估算；Provider 只把实际 promptTokens/totalTokens 记录为整次 GenerationRun，不把估算冒充分项实测。</p>` : '<p class="dim-note">未记录分项 context manifest。不会把估算值冒充 Writer 实际输入。</p>'}</div>${sections.length ? `<div class="sf-inspector-section"><h4>Context sections</h4><div class="sf-context-breakdown">${sectionRows}</div></div>` : ''}${components.length ? `<div class="sf-inspector-section"><h4>Writer prompt components</h4><div class="sf-context-breakdown">${componentRows}</div></div>` : ''}<div class="sf-inspector-section"><h4>GenerationRun</h4><dl class="sf-kv"><dt>Run</dt><dd>${text(context.trace?.generationRunId || '—')}</dd><dt>Prompt</dt><dd>${text(tokenSummary.promptTokens ?? '—')} tokens</dd><dt>Total</dt><dd>${text(tokenSummary.totalTokens ?? '—')} tokens</dd><dt>Hash</dt><dd>${text(tokenSummary.promptSha256 || '—')}</dd><dt>Context Graph</dt><dd>${text(graphSnapshot.valid ? `${graphSnapshot.nodeCount || 0} nodes / ${graphSnapshot.edgeCount || 0} edges` : 'manifest-only / unavailable')}</dd><dt>Binding</dt><dd>${text(tokenSummary.contextBinding || 'manifest source list')}</dd></dl></div><div class="sf-inspector-actions"><button class="btn btn-sm btn-secondary" data-sf-back-inspector="1">返回节点</button></div>`;
    if (availabilityRows) {
      inspector.insertAdjacentHTML('afterbegin', `<div class="sf-inspector-section"><h4>Source availability</h4><div class="sf-context-breakdown">${availabilityRows}</div></div>`);
    }
    inspector.insertAdjacentHTML('beforeend', runPicker);
    inspector.insertAdjacentHTML('afterbegin', tokenAttributionBanner);
    const rangeRows = [...sources, ...sections, ...components]
      .map((item) => {
        const range = item.persistedPromptRange || item.promptRange || item.contextRange;
        if (!range) return null;
        const label = item.title || item.label || item.sourceType || item.id || 'context';
        return `${label}: ${range.scope || 'context'} ${range.start}–${range.end} chars (${range.precision || 'recorded'})`;
      })
      .filter(Boolean)
      .slice(0, 24);
    if (rangeRows.length) {
      const block = document.createElement('div');
      block.className = 'sf-inspector-section';
      const heading = document.createElement('h4');
      heading.textContent = 'Persisted input ranges';
      block.appendChild(heading);
      const list = document.createElement('ul');
      list.className = 'sf-inspector-list';
      rangeRows.forEach((row) => { const item = document.createElement('li'); item.textContent = row; list.appendChild(item); });
      block.appendChild(list);
      const note = document.createElement('p');
      note.className = 'dim-note';
      note.textContent = '字符区间来自最终 GenerationRun 输入；Provider token 仍只以整次运行的 usage 为准。';
      block.appendChild(note);
      inspector.appendChild(block);
    }
    inspector.querySelectorAll('[data-sf-context-source]').forEach((button) => button.addEventListener('click', () => focusContextEvidence(button.dataset.sfContextSource)));
    const linkedSources = sources.filter((source) => source.nodeId);
    if (linkedSources.length) {
      inspector.insertAdjacentHTML('afterbegin', `<div class="sf-inspector-section sf-context-linked"><h4>定位真实图节点</h4><div class="sf-inspector-actions">${linkedSources.slice(0, 8).map((source) => `<button class="btn btn-sm btn-ghost" data-sf-context-node="${attr(source.nodeId)}">${text(nodeLabel(source.type))} · ${text(source.title)}</button>`).join('')}</div></div>`);
      inspector.querySelectorAll('[data-sf-context-node]').forEach((button) => button.addEventListener('click', () => focusContextEvidence(button.dataset.sfContextNode)));
    }
    inspector.querySelector('[data-sf-context-run]')?.addEventListener('change', (event) => {
      const runId = event.target?.value || '';
      if (runId && runId !== String(context.trace?.selectedRunId || context.trace?.generationRunId || '')) {
        loadContext(context.chapterId, runId, state.depth);
      }
    });
    inspector.querySelector('[data-sf-back-inspector]')?.addEventListener('click', () => {
      state.contextEvidence = null;
      renderInspector();
    });
  }

  function openNodeAction(node) {
    if (node.type === 'Chapter') {
      const number = node.metadata?.number;
      if (number != null && typeof window.openChapterStudioAction === 'function') window.openChapterStudioAction('open', number);
      else if (number != null && typeof window.viewChapter === 'function') window.viewChapter(number);
      else go('chapters');
      return;
    }
    if (node.type === 'Character') { openStoryFlowView('character', node.id); return; }
    if (node.type === 'Foreshadow') { openStoryFlowView('foreshadow', node.id); return; }
    if (node.type === 'Location' || node.type === 'Faction') { openStoryFlowView('world', node.id); return; }
    if (node.type === 'StoryBibleEntry') {
      const stepNumber = Number(node.metadata?.stepNumber);
      if (Number.isFinite(stepNumber) && typeof S !== 'undefined') S._wizardStepNumber = stepNumber;
      go('wizard');
      return;
    }
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
        const keepFullGraphViewport = state.view === 'all'
          && Boolean(canvasViewportBounds())
          && Boolean(state.graph?.meta?.viewport?.requested);
        state.focus = id;
        state.depth = 1;
        // Search is a navigation action inside the active projection.  In an
        // expanded Full Graph, replacing the bounded graph with an unbounded
        // focused response would discard the viewport/boundary contract and
        // make the selected node's Inspector lose its semantic edge page.
        // Keep that projection alive and inject the searched root through the
        // bounded read path instead.  Other views retain their type-specific
        // focus behavior.
        if (!keepFullGraphViewport) state.view = TYPE_VIEW[button.dataset.sfSearchType] || 'story';
        else state.presentationMode = 'expanded';
        state.selected = new Set([id]);
         state.detail = null;
         state.history = null;
         state.canonicalReplay = null;
         state.canonicalDiff = null;
         hideSearchResults();
        if (keepFullGraphViewport) loadFocusedSearchResultInViewport(id, canvasViewportBounds());
        else loadGraph();
      }));
    } catch (error) {
      results.innerHTML = `<p class="dim-note" style="padding:8px">搜索失败：${text(error.message)}</p>`;
      toast(`Story Graph 搜索失败：${error.message}`, 'error');
    }
  }

  function queryString(viewport = null, pageToken = '', boundaryPageToken = '', boundaryNodeId = '', edgePageToken = '') {
    // Full Graph is an explicit viewport-driven view.  Keep its initial
    // response on the same bounded working-set budget as a viewport page so
    // a large book does not serialize a 1200-node/3000-edge compatibility
    // payload before the Canvas has even established its visible rectangle.
    const boundedLimit = '240';
    const params = new URLSearchParams({ view: state.view, depth: String(state.depth), limit: boundedLimit });
    if (state.view === 'all') params.set('edge_limit', '600');
    if (state.view === 'character' || state.view === 'story' || state.view === 'all') params.set('presentation', state.presentationMode || 'clustered');
    if (state.focus) params.set('focus', state.focus);
    if (state.types.length) params.set('types', state.types.join(','));
    if (state.statuses.length) params.set('statuses', state.statuses.join(','));
    if (state.chapterFrom) params.set('chapter_from', String(state.chapterFrom));
    if (state.chapterTo) params.set('chapter_to', String(state.chapterTo));
    if (state.volumeNumber) params.set('volume', String(state.volumeNumber));
    if (state.timeFrom) params.set('time_from', state.timeFrom);
    if (state.timeTo) params.set('time_to', state.timeTo);
    if (state.plotThread) params.set('plot_thread', state.plotThread);
    if (viewport) {
      params.set('x_from', String(viewport.xFrom));
      params.set('x_to', String(viewport.xTo));
      params.set('y_from', String(viewport.yFrom));
      params.set('y_to', String(viewport.yTo));
      params.set('viewport_padding', '0');
      if (pageToken) params.set('page_token', pageToken);
      if (edgePageToken) params.set('edge_page_token', edgePageToken);
      if (boundaryPageToken) params.set('boundary_page_token', boundaryPageToken);
      if (boundaryNodeId) params.set('boundary_node_id', boundaryNodeId);
    }
    return params.toString();
  }

  function canvasViewportBounds() {
    const canvas = document.getElementById('sf-canvas');
    if (!canvas || !state?.transform) return null;
    const rect = canvas.getBoundingClientRect();
    const scale = Math.max(Number(state.transform.scale) || 1, 0.01);
    // Fetch a little beyond the visible rectangle so a short pan does not
    // cause a request for every pointer movement. The server still applies
    // the same world-coordinate boundary to its authoritative projection.
    const buffer = 520 / scale;
    return {
      xFrom: (-state.transform.tx / scale) - buffer,
      xTo: ((rect.width - state.transform.tx) / scale) + buffer,
      yFrom: (-state.transform.ty / scale) - buffer,
      yTo: ((rect.height - state.transform.ty) / scale) + buffer,
    };
  }

  function viewportKey(viewport) {
    if (!viewport) return '';
    return ['xFrom', 'xTo', 'yFrom', 'yTo']
      .map((key) => Math.round(Number(viewport[key]) * 10) / 10)
      .join(':');
  }

  function viewportBoundsFromMetadata(viewport) {
    if (!viewport || [viewport.xFrom, viewport.xTo, viewport.yFrom, viewport.yTo].some((value) => value == null)) return null;
    return {
      xFrom: Number(viewport.xFrom),
      xTo: Number(viewport.xTo),
      yFrom: Number(viewport.yFrom),
      yTo: Number(viewport.yTo),
    };
  }

  function viewportFetchEnabled() {
    return Boolean(
      state
      && !state.loading
      && state.view === 'all'
      && state.presentationMode === 'expanded'
      && state.graph?.meta
      && Number(state.graph.meta.totalAvailableNodes || 0) > Number(state.graph.nodes?.length || 0),
    );
  }

  function scheduleViewportFetch() {
    if (!viewportFetchEnabled()) return;
    const viewport = canvasViewportBounds();
    const key = viewportKey(viewport);
    if (!viewport || !key || key === state.viewportRequestKey) return;
    const continuation = currentViewportContinuation(key);
    if (!continuation && state.viewportPages?.has(key)) return;
    window.clearTimeout(state.viewportFetchTimer);
    state.viewportFetchTimer = window.setTimeout(() => loadViewport(viewport, key), 180);
  }

  function currentViewportContinuation(key = viewportKey(canvasViewportBounds())) {
    const continuation = state?.viewportContinuation;
    if (!continuation?.key || continuation.key !== key) return null;
    return continuation;
  }

  function currentViewportEdgeContinuation(key = viewportKey(canvasViewportBounds())) {
    const continuation = state?.viewportEdgeContinuation;
    if (!continuation?.key || continuation.key !== key) return null;
    return continuation;
  }

  function mergeViewportGraph(base, page) {
    if (!base || !Array.isArray(base.nodes)) return page;
    const nodesById = new Map();
    const nodeOrder = [];
    base.nodes.forEach((node) => {
      nodesById.set(node.id, node);
      nodeOrder.push(node.id);
    });
    const preserveLocalLayout = Boolean(state.layoutDirty);
    const layoutKeys = ['x', 'y', 'collapsed', 'pinned', 'hidden'];
    (page.nodes || []).forEach((node) => {
      const previous = nodesById.get(node.id);
      if (!previous) {
        nodesById.set(node.id, node);
        nodeOrder.push(node.id);
        return;
      }
      const merged = { ...previous, ...node };
      // A viewport read must not overwrite unsaved workspace coordinates or
      // visibility state. Those are UI state, not Story Graph canon.
      if (preserveLocalLayout) {
        layoutKeys.forEach((key) => {
          if (Object.prototype.hasOwnProperty.call(previous, key)) merged[key] = previous[key];
        });
      }
      nodesById.set(node.id, merged);
    });

    const baseMeta = base.meta || {};
    const pageMeta = page.meta || {};
    const baseViewport = baseMeta.viewport || {};
    const pageViewport = pageMeta.viewport || {};
    const enteringViewportProjection = Boolean(pageViewport.requested) && !Boolean(baseViewport.requested);
    const edgesById = new Map();
    const edgeOrder = [];
    const edgeKey = (edge) => edge.id || `${edge.source}:${edge.type}:${edge.target}:${edge.label || ''}`;
    if (!enteringViewportProjection) {
      (base.edges || []).forEach((edge) => {
        const key = edgeKey(edge);
        edgesById.set(key, edge);
        edgeOrder.push(key);
      });
    }
    (page.edges || []).forEach((edge) => {
      const key = edgeKey(edge);
      if (!edgesById.has(key)) edgeOrder.push(key);
      edgesById.set(key, edge);
    });

    const pageHasExplicitBoundaryPage = Object.prototype.hasOwnProperty.call(pageViewport, 'boundaryPageOffset')
      || Object.prototype.hasOwnProperty.call(pageViewport, 'boundaryPageSize')
      || Object.prototype.hasOwnProperty.call(pageViewport, 'nextBoundaryPageToken');
    // A normal viewport continuation has no boundary cursor.  Once the user
    // explicitly asks for a node's semantic boundary page, an asynchronous
    // normal viewport fetch must not erase that cursor before the Inspector
    // can request its next page.
    const preserveBoundaryPage = Boolean(state.boundaryNodeId)
      && state.selected?.has(state.boundaryNodeId)
      && !pageHasExplicitBoundaryPage;
    const totalAvailableNodes = Math.max(
      Number(baseMeta.totalAvailableNodes || 0),
      Number(pageMeta.totalAvailableNodes || 0),
    );
    const totalAvailableEdges = Math.max(
      Number(baseMeta.totalAvailableEdges || 0),
      Number(pageMeta.totalAvailableEdges || 0),
    );
    const nodes = nodeOrder.map((id) => nodesById.get(id)).filter(Boolean);
    const edges = edgeOrder.map((id) => edgesById.get(id)).filter(Boolean);
    return {
      ...base,
      ...page,
      nodes,
      edges,
      meta: {
        ...baseMeta,
        ...pageMeta,
        totalAvailableNodes: totalAvailableNodes || pageMeta.totalAvailableNodes || baseMeta.totalAvailableNodes,
        totalAvailableEdges: totalAvailableEdges || pageMeta.totalAvailableEdges || baseMeta.totalAvailableEdges,
        returnedNodes: nodes.length,
        returnedEdges: edges.length,
        truncated: totalAvailableNodes ? nodes.length < totalAvailableNodes : pageMeta.truncated,
        viewport: {
          ...baseViewport,
          ...pageViewport,
          requested: true,
          incrementalMerge: true,
          loadedNodeCount: nodes.length,
          loadedEdgeCount: edges.length,
          loadedInternalEdgeCount: edges.length,
          internalEdgeCount: Math.max(
            Number(baseViewport.internalEdgeCount || 0),
            Number(pageViewport.internalEdgeCount || 0),
          ),
          internalEdgeScope: pageViewport.internalEdgeScope || baseViewport.internalEdgeScope || 'viewport_candidate_set',
          returnedInternalEdges: Number(pageViewport.returnedInternalEdges || 0),
          internalEdgesTruncated: Boolean(pageViewport.internalEdgesTruncated),
          internalEdgePageSize: Number(pageViewport.internalEdgePageSize || baseViewport.internalEdgePageSize || 0),
          internalEdgePageOffset: Number(pageViewport.internalEdgePageOffset || 0),
          internalEdgePageIndex: Number(pageViewport.internalEdgePageIndex || 0),
          nextInternalEdgePageToken: pageViewport.nextInternalEdgePageToken || null,
          pagesLoaded: state.viewportPages?.size || 0,
          returnedInViewport: pageViewport.returnedInViewport || page.nodes?.length || 0,
          crossBoundaryEdgeCount: preserveBoundaryPage
            ? Number(baseViewport.crossBoundaryEdgeCount || 0)
            : pageViewport.crossBoundaryEdgeCount || 0,
          returnedCrossBoundaryEdges: preserveBoundaryPage
            ? Number(baseViewport.returnedCrossBoundaryEdges || 0)
            : pageViewport.returnedCrossBoundaryEdges || 0,
          crossBoundaryEdgesTruncated: preserveBoundaryPage
            ? Boolean(baseViewport.crossBoundaryEdgesTruncated)
            : Boolean(pageViewport.crossBoundaryEdgesTruncated),
          crossBoundaryEdgeTypeCounts: preserveBoundaryPage
            ? (baseViewport.crossBoundaryEdgeTypeCounts || {})
            : (pageViewport.crossBoundaryEdgeTypeCounts || {}),
          crossBoundaryEdges: preserveBoundaryPage
            ? (Array.isArray(baseViewport.crossBoundaryEdges) ? baseViewport.crossBoundaryEdges : [])
            : (Array.isArray(pageViewport.crossBoundaryEdges) ? pageViewport.crossBoundaryEdges : []),
          boundaryPageOffset: preserveBoundaryPage
            ? Number(baseViewport.boundaryPageOffset || 0)
            : Number(pageViewport.boundaryPageOffset || 0),
          boundaryPageIndex: preserveBoundaryPage
            ? Number(baseViewport.boundaryPageIndex || 0)
            : Number(pageViewport.boundaryPageIndex || 0),
          boundaryHasMore: preserveBoundaryPage
            ? Boolean(baseViewport.boundaryHasMore)
            : Boolean(pageViewport.boundaryHasMore),
          nextBoundaryPageToken: preserveBoundaryPage
            ? (baseViewport.nextBoundaryPageToken || null)
            : (pageViewport.nextBoundaryPageToken || null),
       },
      },
    };
  }

  async function loadViewport(viewport, key) {
    if (!viewportFetchEnabled() || !viewport || !key) return;
    if (state.viewportFetchInFlight) return;
    const requestGeneration = Number(state.viewportGeneration || 0);
    const enteringViewport = state.viewportLastKey !== key;
    if (enteringViewport) state.viewportEdgeExhausted = false;
    const continuation = currentViewportContinuation(key);
    const edgeContinuation = currentViewportEdgeContinuation(key);
    const edgeWasExhausted = Boolean(state.viewportEdgeExhausted && !edgeContinuation);
    const requestKey = continuation?.token ? `${key}:page:${continuation.offset}` : key;
    if (state.viewportPages?.has(requestKey)) return;
    state.viewportRequestKey = requestKey;
    state.viewportFetchInFlight = true;
    try {
      const graph = await api('GET', `/books/${currentBook()}/story-graph?${queryString(viewport, continuation?.token || '', '', '', edgeContinuation?.token || '')}`);
      // A search focus or explicit view reload can invalidate a normal
      // viewport request while it is in flight.  Its old page token belongs
      // to the previous focus/query and must never update the new read model.
      if (!state || requestGeneration !== Number(state.viewportGeneration || 0) || state.view !== 'all') return;
      state.viewportFetchError = null;
      state.viewportLastKey = key;
      state.viewportPages?.add(requestKey);
      const page = graph.meta?.viewport || {};
      state.viewportContinuation = page.hasMore && page.nextPageToken
        ? {
          key,
          token: page.nextPageToken,
          offset: Number(page.pageOffset || 0) + Number(page.returnedInViewport || graph.nodes?.length || 0),
        }
        : null;
      state.viewportEdgeContinuation = edgeWasExhausted || (state.viewportEdgeExhausted && !edgeContinuation)
        ? null
        : page.internalEdgesTruncated && page.nextInternalEdgePageToken
          ? {
            key,
            token: page.nextInternalEdgePageToken,
            offset: Number(page.internalEdgePageOffset || 0) + Number(page.returnedInternalEdges || graph.edges?.length || 0),
          }
          : null;
      if (!edgeWasExhausted) {
        state.viewportEdgeExhausted = !page.internalEdgesTruncated
          && Number(page.internalEdgeCount || 0) <= Number(page.internalEdgePageOffset || 0) + Number(page.returnedInternalEdges || 0);
      }
      // An empty viewport must not replace the interactive Canvas with a
      // dead-end empty state. Keep the previous bounded page until the user
      // pans back into a populated region.
      if (Array.isArray(graph.nodes) && graph.nodes.length) {
        state.graph = mergeViewportGraph(state.graph, graph);
        if (edgeWasExhausted) {
          const previousViewport = state.graph?.meta?.viewport || {};
          state.graph.meta.viewport = {
            ...previousViewport,
            internalEdgesTruncated: false,
            nextInternalEdgePageToken: null,
            internalEdgePageOffset: Number(previousViewport.internalEdgePageOffset || 0),
            internalEdgePageIndex: Number(previousViewport.internalEdgePageIndex || 0),
            loadedInternalEdgeCount: state.graph.edges?.length || 0,
          };
        }
        rememberGraphSnapshot(state.graph);
        renderToolbar();
        renderSidebar();
        renderCanvas();
        renderInspector();
      }
    } catch (error) {
      if (requestGeneration !== Number(state.viewportGeneration || 0)) return;
      state.viewportFetchError = error.message;
      // Keep the current graph visible; the error remains observable in the
      // toolbar badge instead of being silently swallowed.
      renderToolbar();
    } finally {
      if (requestGeneration !== Number(state.viewportGeneration || 0)) return;
      state.viewportFetchInFlight = false;
      state.viewportRequestKey = '';
      const currentKey = viewportKey(canvasViewportBounds());
      if (currentKey && currentKey !== state.viewportLastKey && !state.viewportFetchError) scheduleViewportFetch();
    }
  }

  async function loadNextViewportPage() {
    const viewport = canvasViewportBounds();
    const key = viewportKey(viewport);
    if (!viewport || !key || !currentViewportContinuation(key)) return;
    await loadViewport(viewport, key);
  }

  async function loadNextViewportEdgePage() {
    const viewport = canvasViewportBounds();
    const key = viewportKey(viewport);
    const continuation = currentViewportEdgeContinuation(key);
    if (!viewport || !key || !continuation?.token || state.viewportEdgeFetchInFlight) return;
    const requestGeneration = Number(state.viewportGeneration || 0);
    state.viewportEdgeFetchInFlight = true;
    try {
      const graph = await api('GET', `/books/${currentBook()}/story-graph?${queryString(viewport, '', '', '', continuation.token)}`);
      if (!state || requestGeneration !== Number(state.viewportGeneration || 0) || state.view !== 'all') return;
      const page = graph.meta?.viewport || {};
      const previousViewport = state.graph?.meta?.viewport || {};
      state.viewportEdgeContinuation = page.internalEdgesTruncated && page.nextInternalEdgePageToken
        ? {
          key,
          token: page.nextInternalEdgePageToken,
          offset: Number(page.internalEdgePageOffset || 0) + Number(page.returnedInternalEdges || graph.edges?.length || 0),
        }
        : null;
      state.viewportEdgeExhausted = !page.internalEdgesTruncated
        && Number(page.internalEdgeCount || 0) <= Number(page.internalEdgePageOffset || 0) + Number(page.returnedInternalEdges || 0);
      state.graph = mergeViewportGraph(state.graph, graph);
      state.graph.meta.viewport = {
        ...state.graph.meta.viewport,
        pageOffset: Number(previousViewport.pageOffset || 0),
        pageIndex: Number(previousViewport.pageIndex || 0),
        hasMore: Boolean(previousViewport.hasMore),
        nextPageToken: previousViewport.nextPageToken || null,
        internalEdgePageOffset: Number(page.internalEdgePageOffset || 0),
        internalEdgePageIndex: Number(page.internalEdgePageIndex || 0),
        internalEdgesTruncated: Boolean(page.internalEdgesTruncated),
        nextInternalEdgePageToken: page.nextInternalEdgePageToken || null,
        loadedInternalEdgeCount: state.graph.edges?.length || 0,
      };
      rememberGraphSnapshot(state.graph);
      renderToolbar();
      renderSidebar();
      renderCanvas();
      renderInspector();
    } catch (error) {
      state.viewportFetchError = error.message;
      renderToolbar();
    } finally {
      if (state) state.viewportEdgeFetchInFlight = false;
    }
  }

  async function loadNextBoundaryPage(nodeId) {
    // Boundary cursors are signed against the exact world-coordinate page
    // that produced them.  The selected-node center/normal viewport fetch can
    // move the Canvas before the user clicks the Inspector action, so reuse
    // the recorded page bounds instead of silently mixing a fresh viewport
    // with the old cursor.
    const viewport = state.boundaryViewport || canvasViewportBounds();
    const key = viewportKey(viewport);
    const meta = state?.graph?.meta?.viewport || {};
    if (!viewport || !key || !nodeId || (!meta.nextBoundaryPageToken && !meta.crossBoundaryEdgesTruncated)) return;
    if (state.boundaryFetchInFlight) return;
    state.boundaryNodeId = nodeId;
    state.boundaryFetchInFlight = true;
    try {
      const boundaryToken = meta.nextBoundaryPageToken || '';
      const graph = await api('GET', `/books/${currentBook()}/story-graph?${queryString(viewport, '', boundaryToken, nodeId)}`);
      const nextMeta = graph.meta?.viewport || {};
      const previous = state.graph?.meta?.viewport || {};
      state.boundaryViewport = viewportBoundsFromMetadata(nextMeta) || viewport;
      // A terminal boundary page must release the preservation latch.  This
      // lets later ordinary viewport merges update their own counters without
      // resurrecting an exhausted boundary cursor.
      state.boundaryNodeId = nextMeta.nextBoundaryPageToken ? nodeId : '';
      state.graph = {
        ...state.graph,
        meta: {
          ...state.graph.meta,
          viewport: {
            ...previous,
            ...nextMeta,
            crossBoundaryEdgeCount: nextMeta.crossBoundaryEdgeCount || previous.crossBoundaryEdgeCount || 0,
          },
        },
      };
      renderToolbar();
      renderInspector();
    } catch (error) {
      state.viewportFetchError = error.message;
      renderToolbar();
    } finally {
      state.boundaryFetchInFlight = false;
    }
  }

  async function loadFocusedSearchResultInViewport(nodeId, viewport) {
    if (!state || !S.book || !nodeId || !viewport) {
      loadGraph();
      return;
    }
    // Invalidate any ordinary page continuation that was issued before this
    // search. Its token is signed against the previous focus/filter query and
    // would otherwise surface a truthful but user-visible 422 after the
    // focused boundary response succeeds.
    state.viewportGeneration = Number(state.viewportGeneration || 0) + 1;
    state.viewportFetchInFlight = false;
    state.viewportEdgeFetchInFlight = false;
    state.viewportContinuation = null;
    state.viewportEdgeContinuation = null;
    state.viewportEdgeExhausted = false;
    state.viewportPages = new Set();
    state.viewportLastKey = '';
    state.viewportRequestKey = '';
    state.loading = true;
    state.boundaryNodeId = nodeId;
    state.viewportFetchError = null;
    try {
      const graph = await api(
        'GET',
        `/books/${currentBook()}/story-graph?${queryString(viewport, '', '', nodeId)}`,
      );
      if (!state || state.view !== 'all') return;
      state.boundaryViewport = viewportBoundsFromMetadata(graph.meta?.viewport) || viewport;
      // This is an explicit search/focus action.  Merge the authoritative
      // target page into the current bounded graph so the target can be
      // inspected even when its world-coordinate position was off-screen.
      state.graph = mergeViewportGraph(state.graph, graph);
      rememberGraphSnapshot(state.graph);
      const focusedViewportKey = viewportKey(viewport);
      state.viewportLastKey = focusedViewportKey;
      if (focusedViewportKey) state.viewportPages.add(focusedViewportKey);
      state.selected = new Set([nodeId]);
      state.edgeSelectedId = null;
      state.detail = null;
      state.history = null;
      state.canonicalReplay = null;
      state.canonicalDiff = null;
      renderToolbar();
      renderSidebar();
      renderCanvas();
      renderInspector();
      loadNodeDetail(nodeId);
    } catch (error) {
      state.viewportFetchError = error.message;
      renderToolbar();
      toast(`StoryFlow search focus failed: ${error.message}`, 'error');
    } finally {
      state.loading = false;
      scheduleViewportFetch();
    }
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

  function rememberGraphSnapshot(graph) {
    const snapshotId = String(graph?.meta?.graphSnapshotId || '').trim();
    if (snapshotId) state.graphSnapshotId = snapshotId;
  }

  async function refreshGraphFromCanon() {
    if (!state || !S.book) return;
    state.graphFreshness = null;
    state.graphFreshnessError = null;
    if (state.view === 'context' && state.contextChapterId) {
      await loadContext(
        state.contextChapterId,
        state.context?.trace?.selectedRunId || '',
        state.depth,
      );
      return;
    }
    await loadGraph();
  }

  async function checkGraphFreshness() {
    if (!state || !S.book || !state.graphSnapshotId || state.loading || state.graphFreshnessLoading || state.graphFreshnessDisabled) return;
    state.graphFreshnessLoading = true;
    try {
      const query = new URLSearchParams({ fromSnapshot: state.graphSnapshotId });
      if (state.focus) query.set('nodeId', state.focus);
      const result = await api('GET', `/books/${currentBook()}/story-graph/changes?${query.toString()}`);
      if (!state) return;
      state.graphFreshnessError = null;
      if (!result.changed) {
        state.graphFreshness = null;
        return;
      }
      state.graphFreshness = result;
      const canAutoRefresh = !state.editMode && !state.connection && !state.layoutDirty;
      if (canAutoRefresh) {
        const changedNodeCount = Number(result.diff?.addedNodes?.length || 0)
          + Number(result.diff?.changedNodes?.length || 0)
          + Number(result.diff?.removedNodes?.length || 0);
        toast(
          `Canon updated in SQLite Story Graph${changedNodeCount ? ` · ${changedNodeCount} node changes` : ''}. Refreshing the focused view.`,
          'success',
        );
        await refreshGraphFromCanon();
      } else {
        renderToolbar();
      }
    } catch (error) {
      if (!state) return;
      state.graphFreshnessError = error.message;
      // Empty projects have a truthful graph projection but no authoritative
      // book yet.  Do not retry the same permanent 409 every 12 seconds;
      // leave the boundary visible and wait for a new workspace load.
      if (error?.status === 409 && /authoritative book/i.test(String(error.message || ''))) {
        state.graphFreshnessDisabled = true;
        window.clearInterval(state.graphFreshnessTimer);
        state.graphFreshnessTimer = null;
      }
      renderToolbar();
    } finally {
      if (state) state.graphFreshnessLoading = false;
    }
  }

  function startGraphFreshnessMonitor() {
    if (!state || state.graphFreshnessTimer) return;
    // A long-lived StoryFlow page must notice an accepted StoryCommit made by
    // Writing Studio, but polling remains bounded and read-only. The server
    // compares immutable observed snapshots instead of diffing browser data.
    state.graphFreshnessTimer = window.setInterval(checkGraphFreshness, 12000);
  }

  async function loadModelReadiness() {
    if (!state || !S.book) return;
    const requestId = Number(state.modelReadinessRequestId || 0) + 1;
    state.modelReadinessRequestId = requestId;
    state.modelReadinessLoading = true;
    renderToolbar();
    try {
      const payload = await api('GET', `/creation/preflight?mode=planned&bookId=${currentBook()}`);
      if (!state || state.modelReadinessRequestId !== requestId) return;
      state.modelReadiness = payload.modelReadiness || {
        ready: payload.ready === true,
        message: payload.ready ? 'StoryFlow model actions are ready.' : 'AI model setup is required before model-backed actions can run.',
      };
    } catch (error) {
      if (!state || state.modelReadinessRequestId !== requestId) return;
      state.modelReadiness = {
        ready: null,
        message: `AI readiness check failed: ${error.message}`,
      };
    } finally {
      if (state && state.modelReadinessRequestId === requestId) {
        state.modelReadinessLoading = false;
        renderToolbar();
        renderInspector();
      }
    }
  }

  async function loadGraph() {
    if (!state || !S.book) return;
    window.clearTimeout(state.viewportFetchTimer);
    state.viewportFetchTimer = null;
    state.viewportLastKey = '';
    state.viewportRequestKey = '';
    state.viewportPages = new Set();
    state.viewportContinuation = null;
    state.viewportEdgeContinuation = null;
    state.viewportEdgeExhausted = false;
    state.viewportEdgeFetchInFlight = false;
    state.viewportFetchError = null;
    state.boundaryNodeId = '';
    state.boundaryViewport = null;
    state.viewportGeneration = Number(state.viewportGeneration || 0) + 1;
    state.selectionProjection = null;
    state.candidateComparison = null;
    state.candidateLineage = null;
    state.loading = true;
    const canvas = document.getElementById('sf-canvas');
    if (canvas) canvas.innerHTML = '<div class="sf-loading">从 SQLite Story Graph 读取焦点子图…</div>';
    try {
      if (state.view === 'context') {
        const contextChapterId = state.contextChapterId || (state.focus && String(state.focus).startsWith('chapter:') ? state.focus : '');
        const loadedContextDepth = Number(state.context?.graph?.meta?.contextDepth || state.context?.graph?.meta?.depth || state.contextDepth || 1);
        if (contextChapterId && (!state.context || state.context.chapterId !== contextChapterId || loadedContextDepth !== Number(state.depth || 1))) {
          await loadContext(contextChapterId, state.context?.trace?.selectedRunId || '', state.depth);
          return;
        }
        if (state.context && state.context.chapterId === contextChapterId) {
          state.graph = state.context.graph || { nodes: [], edges: [], meta: {} };
          rememberGraphSnapshot(state.graph);
          state.focus = state.graph.focus || state.focus;
          state.selected = new Set([...state.selected].filter((id) => state.graph.nodes.some((node) => node.id === id)));
          state.edgeSelectedId = null;
      state.detail = null;
      state.impact = null;
      state.chapterImpact = null;
      state.chapterVersionCompare = null;
      state.history = null;
           state.canonicalReplay = null;
           state.canonicalDiff = null;
           renderToolbar();
          renderSidebar();
          renderCanvas();
          renderInspector();
          renderContextInspector();
          await loadLayoutHistory(false);
          loadCandidateSets();
          loadForecastRecoveryTasks();
          renderToolbar();
          window.requestAnimationFrame(() => {
            const focusedSelection = state.focus && selectedNodes()[0];
            if (focusedSelection) centerOn(focusedSelection);
            else fitGraph();
          });
          loadStoryHealth();
          return;
        }
      }
      const graph = await api('GET', `/books/${currentBook()}/story-graph?${queryString()}`);
      const hadFocus = Boolean(state.focus);
      state.graph = graph;
      rememberGraphSnapshot(graph);
      state.expandedPresentationClusters = new Set();
      applyPresentationLayout();
      state.focus = graph.focus || state.focus;
      state.selected = new Set([...state.selected].filter((id) => graph.nodes.some((node) => node.id === id)));
      if (!hadFocus && state.focus) state.selected = new Set([state.focus]);
      state.edgeSelectedId = null;
      state.detail = null;
       state.impact = null;
       state.history = null;
       state.canonicalReplay = null;
       state.canonicalDiff = null;
       renderToolbar();
      renderSidebar();
      renderCanvas();
      renderInspector();
      await loadLayoutHistory(false);
      renderToolbar();
      window.requestAnimationFrame(() => {
        const focusedSelection = state.focus && selectedNodes()[0];
        if (focusedSelection) centerOn(focusedSelection);
        else fitGraph();
      });
      const selected = selectedNodes()[0];
      if (selected) loadNodeDetail(selected.id);
      if (!state.analysisHistoryLoaded) loadAnalysisHistory();
      loadCandidateSets();
      loadForecastRecoveryTasks();
      loadStoryHealth();
    } catch (error) {
      state.graph = { nodes: [], edges: [], meta: {} };
      const shell = document.getElementById('sf-canvas');
      if (shell) shell.innerHTML = `<div class="sf-canvas-empty"><div><strong>Story Graph 读取失败</strong><span>${text(error.message)}</span></div></div>`;
      renderSidebar();
      renderInspector();
      toast(`StoryFlow 加载失败：${error.message}`, 'error');
    } finally {
      state.loading = false;
      scheduleViewportFetch();
    }
  }

  async function loadCandidateSets() {
    if (!state || !S.book) return;
    state.candidateSetsLoading = true;
    state.candidateSetsError = null;
    try {
      const payload = await api('GET', `/books/${currentBook()}/story-graph/candidates`);
      if (!state) return;
      state.candidateSets = Array.isArray(payload.candidateSets) ? payload.candidateSets : [];
      state.candidateSetsRevision = Number(payload.revision || 1);
    } catch (error) {
      if (!state) return;
      state.candidateSets = [];
      state.candidateSetsError = error.message;
    } finally {
      if (state) {
        state.candidateSetsLoading = false;
        renderSidebar();
      }
    }
  }

  async function loadForecastRecoveryTasks() {
    if (!state || !S.book) return;
    state.recoverableForecastTasksLoading = true;
    state.recoverableForecastTasksError = null;
    try {
      const payload = await api('GET', `/books/${currentBook()}/story-graph/candidates/recoverable-tasks`);
      if (!state) return;
      state.recoverableForecastTasks = Array.isArray(payload.tasks) ? payload.tasks : [];
    } catch (error) {
      if (!state) return;
      state.recoverableForecastTasks = [];
      state.recoverableForecastTasksError = error.message;
    } finally {
      if (state) {
        state.recoverableForecastTasksLoading = false;
        renderSidebar();
      }
    }
  }

  function storyHealthView(type) {
    return {
      Character: 'character',
      PlotThread: 'story',
      Foreshadow: 'foreshadow',
    }[type] || 'story';
  }

  function focusStoryHealth(id, type) {
    if (!state || !id) return;
    state.view = storyHealthView(type);
    state.focus = id;
    state.selected = new Set([id]);
    // A health signal is an explicit navigation request. Clear filters that
    // could otherwise hide the target before the projector can inject it as
    // the focused root.
    state.types = [];
    state.statuses = [];
    state.chapterFrom = '';
    state.chapterTo = '';
    state.volumeNumber = '';
    state.timeFrom = '';
    state.timeTo = '';
    state.plotThread = '';
    state.detail = null;
    state.edgeSelectedId = null;
    loadGraph();
  }

  async function loadStoryHealth() {
    if (!state || !S.book) return;
    const requestId = Number(state.storyHealthRequestId || 0) + 1;
    state.storyHealthRequestId = requestId;
    state.storyHealthLoading = true;
    state.storyHealthError = null;
    renderSidebar();
    try {
      const query = new URLSearchParams({ lookback: String(state.storyHealthLookback || 8), limit: '50' });
      const payload = await api('GET', `/books/${currentBook()}/story-graph/health?${query.toString()}`);
      if (!state || state.storyHealthRequestId !== requestId) return;
      state.storyHealth = payload;
    } catch (error) {
      if (!state || state.storyHealthRequestId !== requestId) return;
      state.storyHealth = null;
      state.storyHealthError = error.message;
    } finally {
      if (state && state.storyHealthRequestId === requestId) {
        state.storyHealthLoading = false;
        renderSidebar();
      }
    }
  }

  function renderStoryHealthSection() {
    const payload = state.storyHealth;
    if (state.storyHealthLoading && !payload) {
      return '<div class="sf-filter-block sf-story-health-section"><div class="sf-panel-title"><span>故事健康</span><small>读取中</small></div><p class="dim-note">从 SQLite 生命周期和章节出现记录读取…</p></div>';
    }
    if (state.storyHealthError) {
      return `<div class="sf-filter-block sf-story-health-section"><div class="sf-panel-title"><span>故事健康</span><small>读取失败</small></div><div class="sf-health-read-error">${text(state.storyHealthError)}</div><p class="dim-note">健康信号不会被静默吞掉；来源：Story Graph read model。</p></div>`;
    }
    if (!payload) return '';
    const summary = payload.summary || {};
    const items = Array.isArray(payload.items) ? payload.items : [];
    const labels = {
      stalled_plot_thread: '剧情线停滞',
      unresolved_foreshadow: '伏笔未回收',
      inactive_character: '人物未推进',
    };
    const rows = items.slice(0, 8).map((item) => {
      const last = item.lastActivityChapter ? `最近 Ch.${item.lastActivityChapter}` : '未记录出场';
      const evidence = item.evidenceStatus === 'recorded' ? '有明确证据' : '仅实体投影';
      return `<button class="sf-health-item" data-sf-health-focus="${attr(item.id)}" data-sf-health-type="${attr(item.type)}" title="聚焦 ${attr(item.title)}">
        <span class="sf-health-item-main"><b>${text(item.title || item.id)}</b><small>${text(labels[item.category] || item.category || item.type)} · ${text(last)}</small></span>
        <span class="sf-health-item-meta"><strong>${text(item.gapChapters || 0)}章</strong><small>${text(evidence)}</small></span>
      </button>`;
    }).join('');
    return `<div class="sf-filter-block sf-story-health-section">
      <div class="sf-panel-title"><span>故事健康</span><small>只读 · Ch.${text(payload.currentChapter || '—')}</small></div>
      <div class="sf-health-summary" aria-label="故事健康摘要">
        <span><b>${text(summary.stalledPlotThreads || 0)}</b><small>停滞剧情线</small></span>
        <span><b>${text(summary.unresolvedForeshadows || 0)}</b><small>未回收伏笔</small></span>
        <span><b>${text(summary.inactiveCharacters || 0)}</b><small>未推进人物</small></span>
      </div>
      ${rows || '<p class="dim-note">没有超过当前 lookback 阈值的已记录信号。</p>'}
      ${items.length > 8 ? `<p class="dim-note">仅显示前 8 项；${text(items.length)} 项可通过 Graph API 读取。</p>` : ''}
      <p class="dim-note sf-health-boundary">依据：明确 lifecycle event、章节出现字段和语义边的章节证据；不使用 AI 推断，不写入 Canon。</p>
    </div>`;
  }

  async function loadCandidateComparison(candidateSetId, branchIds = []) {
    if (!state || !S.book || !candidateSetId) return;
    state.candidateComparison = {
      candidateSetId,
      branchIds,
      loading: true,
      error: null,
    };
    state.edgeSelectedId = null;
    state.detail = null;
    renderInspector();
    try {
      const query = new URLSearchParams({ candidateSetId });
      if (branchIds.length) query.set('branchIds', branchIds.join(','));
      const payload = await api('GET', `/books/${currentBook()}/story-graph/candidates/compare?${query.toString()}`);
      if (!state) return;
      state.candidateComparison = {
        ...(payload.comparison || {}),
        loading: false,
        error: null,
      };
    } catch (error) {
      if (!state) return;
      state.candidateComparison = {
        candidateSetId,
        branchIds,
        loading: false,
        error: error.message,
      };
    }
    renderInspector();
  }

  async function planningRevision() {
    const payload = await api('GET', `/books/${currentBook()}/story-graph/planning`);
    return Number(payload.revision || 1);
  }

  async function loadReconciliationCandidates(nodeId) {
    if (!state || !nodeId || !S.book) return;
    state.reconciliationCandidates = { nodeId, loading: true, error: null, candidates: [] };
    renderInspector();
    try {
      const query = new URLSearchParams({ planNodeId: nodeId, limit: '20' });
      const payload = await api('GET', `/books/${currentBook()}/story-graph/planning/reconciliation-candidates?${query.toString()}`);
      if (!state || state.reconciliationCandidates?.nodeId !== nodeId) return;
      state.reconciliationCandidates = {
        nodeId,
        loading: false,
        error: null,
        candidates: Array.isArray(payload.candidates) ? payload.candidates : [],
      };
    } catch (error) {
      if (!state || state.reconciliationCandidates?.nodeId !== nodeId) return;
      state.reconciliationCandidates = { nodeId, loading: false, error: error.message, candidates: [] };
    }
    renderInspector();
  }

  async function reconcilePlanningTask(taskId) {
    if (!state || !taskId || !requirePlanningEditMode()) return;
    const nodeId = state.reconciliationCandidates?.nodeId || state.selected.values().next().value;
    try {
      const revision = await planningRevision();
      await api('POST', `/books/${currentBook()}/story-graph/planning/reconcile`, {
        taskId,
        expectedRevision: revision,
      });
      state.reconciliationCandidates = null;
      await loadGraph();
      if (nodeId && state.selected.has(nodeId)) loadNodeDetail(nodeId);
      toast('已从真实写作任务结果完成规划 overlay 回写；Canon 未重复写入。', 'success');
    } catch (error) {
      toast(`规划 overlay 回写失败：${error.message}`, 'error');
      if (nodeId) loadReconciliationCandidates(nodeId);
    }
  }

  function renderReconciliationBlock(node) {
    if (node?.type !== 'PlanningNode') return '';
    const result = state.reconciliationCandidates?.nodeId === node.id
      ? state.reconciliationCandidates
      : null;
    if (!result || (!result.loading && !result.error && !result.candidates?.length)) return '';
    if (result.loading) {
      return '<div class="sf-inspector-section sf-reconciliation"><h4>Canon 后规划回写</h4><p class="dim-note">正在读取已完成写作任务的恢复信息…</p></div>';
    }
    if (result.error) {
      return `<div class="sf-inspector-section sf-reconciliation"><h4>Canon 后规划回写</h4><p class="dim-note">${text(result.error)}</p></div>`;
    }
    const rows = result.candidates.map((candidate) => `<li><span><b>${text(candidate.taskId)}</b><small>Ch ${text(candidate.chapterNumber || '—')} · Commit ${text(candidate.storyCommitId || '—')}<br>${text(candidate.error || 'overlay retry available')}</small></span><button class="btn btn-sm btn-secondary" data-sf-reconcile-task="${attr(candidate.taskId)}" ${state.editMode ? '' : 'disabled aria-disabled="true"'}>重试回写</button></li>`).join('');
    return `<div class="sf-inspector-section sf-reconciliation"><h4>Canon 后规划回写</h4><div class="sf-context-banner sf-context-excluded"><b>ACCEPTED_PENDING_OVERLAY</b><br>Canon 已经由 StoryCommit 接受，但规划 overlay 尚未完成回写。下面的操作只会重试 revisioned planning overlay，不会重新写入 StoryFact、StoryState 或 StoryCommit。</div><ul class="sf-inspector-list">${rows}</ul>${state.editMode ? '' : '<p class="dim-note">请先切换到“规划编辑”模式，才可重试规划 overlay。</p>'}</div>`;
  }

  function planningAnchorRelation(node) {
    const type = String(node?.type || '');
    const relations = {
      Chapter: { type: 'originates_from', label: '起源于章节' },
      Event: { type: 'originates_from', label: '起源于事件' },
      Character: { type: 'originates_from', label: '起源于人物' },
      PlanningNode: { type: 'originates_from', label: '起源于规划节点' },
      Foreshadow: { type: 'planned_for', label: '规划对应伏笔' },
      PlotThread: { type: 'planned_for', label: '规划对应剧情线' },
      StoryGoal: { type: 'planned_for', label: '规划对应故事目标' },
      StoryBibleEntry: { type: 'depends_on', label: '依赖故事设定' },
      Fact: { type: 'depends_on', label: '依赖事实' },
      Knowledge: { type: 'depends_on', label: '依赖知识' },
      Location: { type: 'affects', label: '影响地点' },
      Faction: { type: 'affects', label: '影响势力' },
    };
    return relations[type] || null;
  }

  function createPlanningNode() {
    if (!requirePlanningEditMode()) return;
    if (typeof modal !== 'function') {
      toast('当前 Studio 没有可用的规划节点编辑器。', 'error');
      return;
    }
    const anchor = selectedNodes()[0];
    const anchorLabel = anchor ? `${nodeLabel(anchor.type)} · ${anchor.title}` : '当前焦点子图';
    const anchorRelation = planningAnchorRelation(anchor);
    const anchorLinkField = anchorRelation
      ? `<label class="fld"><input type="checkbox" id="sf-planning-node-link" checked> 创建后建立“${text(anchorRelation.label)} · ${text(anchorRelation.type)}”语义边</label>`
      : '<div class="sf-context-banner">当前锚点类型没有预设的自动语义边；节点仍可创建，之后可通过 Story Ports 手动连接。</div>';
    modal(`<div class="modal-header"><div><h3>新建 StoryFlow 规划节点</h3><p class="dim-note">写入 revisioned planning overlay，不会创建 StoryFact。</p></div><button class="close-x" onclick="closeModal()">×</button></div>
      <label class="fld">标题<input class="input" id="sf-planning-node-title" maxlength="160" placeholder="例如：黑市交易暴露天玄令"></label>
      <label class="fld">摘要<textarea class="input textarea" id="sf-planning-node-summary" maxlength="2000" placeholder="这个节点希望推动什么故事状态？"></textarea></label>
      <label class="fld">规划状态<select class="input" id="sf-planning-node-status"><option value="PLANNED">PLANNED · 已纳入计划</option><option value="CANDIDATE">CANDIDATE · 候选分支</option><option value="DRAFT">DRAFT · 草稿</option></select></label>
      <div class="sf-context-banner">锚点：${text(anchorLabel)}。语义边只写入 planning overlay，不会改变已发生事实。</div>
      ${anchorLinkField}
      <div class="row row-wrap mt16"><button class="btn btn-primary" id="sf-planning-node-submit">创建规划节点</button><button class="btn btn-ghost" onclick="closeModal()">取消</button></div>`);
    const submit = document.getElementById('sf-planning-node-submit');
    submit?.addEventListener('click', async () => {
      if (!requirePlanningEditMode()) return;
      const title = document.getElementById('sf-planning-node-title')?.value.trim() || '';
      const summary = document.getElementById('sf-planning-node-summary')?.value.trim() || '';
      const status = document.getElementById('sf-planning-node-status')?.value || 'PLANNED';
      const linkToAnchor = Boolean(document.getElementById('sf-planning-node-link')?.checked && anchor && anchorRelation);
      if (!title) {
        toast('规划节点标题不能为空。', 'warning');
        return;
      }
      submit.disabled = true;
        try {
        const revision = await planningRevision();
        const result = await api('POST', `/books/${currentBook()}/story-graph/planning/node`, {
          title,
          summary,
          subtype: 'author-flow-node',
          status,
          source: 'author',
          metadata: {
            createdFrom: 'storyflow-canvas',
            anchorNodeId: anchor?.id || null,
            anchorNodeType: anchor?.type || null,
          },
          anchorNodeId: linkToAnchor ? anchor.id : null,
          anchorEdgeType: linkToAnchor ? anchorRelation.type : null,
          anchorLabel: linkToAnchor ? anchorRelation.label : '',
          anchorMetadata: linkToAnchor ? {
            createdFrom: 'storyflow-canvas',
            anchorNodeId: anchor.id,
            anchorNodeType: anchor.type,
          } : {},
          expectedRevision: revision,
        });
        const createdNode = result.node;
        const linked = Boolean(result.anchorEdge);
        if (typeof closeModal === 'function') closeModal();
        state.selected = createdNode?.id ? new Set([createdNode.id]) : state.selected;
        state.focus = createdNode?.id || state.focus;
        state.detail = null;
        await loadGraph();
        toast(`已创建 ${status} 规划节点：${title}${linked ? '，并建立了合法语义锚点' : ''}。它仍属于 planning overlay。`, 'success');
      } catch (error) {
        submit.disabled = false;
        toast(`规划节点创建失败，节点与锚点边均未写入：${error.message}`, 'error');
      }
    });
  }

  function intentArray(value) {
    return Array.isArray(value)
      ? [...new Set(value.filter((item) => item != null && String(item).trim()).map((item) => String(item)))]
      : [];
  }

  function intentPreviewSection(title, values, empty = '未记录') {
    const items = intentArray(values);
    return `<section class="sf-intent-preview-section"><h4>${text(title)}</h4>${items.length ? `<ul>${items.map((item) => `<li>${text(item)}</li>`).join('')}</ul>` : `<p class="dim-note">${text(empty)}</p>`}</section>`;
  }

  function openChapterIntentPreview(action) {
    if (!requirePlanningEditMode()) return;
    const ids = selectedNodes().map((node) => node.id);
    if (!ids.length) {
      toast('请先在画布上选择至少一个真实 StoryFlow 节点。', 'warning');
      return;
    }
    const mode = action === 'generate' ? 'generate' : 'save';
    const triggerSelector = `[data-sf-action="${mode === 'generate' ? 'generate-chapter' : 'generate-intent'}"]`;
    const trigger = document.querySelector(triggerSelector);
    if (trigger) {
      trigger.disabled = true;
      trigger.setAttribute('aria-busy', 'true');
    }
    toast('正在从 Story Graph 读取结构化 Chapter Intent…', '');
    planningRevision()
      .then((revision) => api('POST', `/books/${currentBook()}/story-graph/planning/intent`, {
        nodeIds: ids,
        save: false,
        expectedRevision: revision,
      }))
      .then((result) => renderChapterIntentPreview(mode, ids, result))
      .catch((error) => toast(`章节 Intent 预览失败：${error.message}`, 'error'))
      .finally(() => {
        const currentTrigger = document.querySelector(triggerSelector);
        if (currentTrigger) {
          currentTrigger.disabled = false;
          currentTrigger.removeAttribute('aria-busy');
        }
      });
  }

  function renderChapterIntentPreview(mode, nodeIds, result) {
    const intent = result?.intent || {};
    const defaultChapter = Number(intent.chapterNumber || intent.chapter_number || 0) || '';
    const title = mode === 'generate' ? '确认章节 Intent · 生成下一章' : '确认章节 Intent · 保存为计划';
    const sourceTitles = nodeIds.map((id) => nodeById(id)?.title || id).slice(0, 12);
    const sourceMarkup = sourceTitles.length
      ? sourceTitles.map((item) => `<span class="sf-intent-source-chip">${text(item)}</span>`).join('')
      : '<span class="dim-note">未记录来源节点</span>';
    modal(`<div class="modal-header"><div><h3>${text(title)}</h3><p class="dim-note">以下内容来自真实 Story Graph 选中节点；此处仍是只读预览，尚未写入 planning overlay 或 Canon。</p></div><button class="close-x" onclick="closeModal()">×</button></div>
      <form id="sf-intent-preview-form" class="sf-intent-preview">
        <div class="sf-intent-preview-boundary"><b>PLANNED Chapter Intent</b><span>${text(nodeIds.length)} 个来源节点</span><div class="sf-intent-source-list">${sourceMarkup}</div></div>
        <label class="sf-intent-field">目标章节<input name="chapterNumber" type="number" min="1" value="${attr(defaultChapter)}" required><small>保存计划可指定未来章节；生成章节时后端仍只允许追加当前下一章。</small></label>
        <div class="sf-intent-preview-grid">
          ${intentPreviewSection('Goal', [intent.goal, ...intentArray(intent.goals)])}
          ${intentPreviewSection('Required Characters', intent.requiredCharacters || intent.required_characters)}
          ${intentPreviewSection('Locations', intent.requiredLocations || intent.required_locations || intent.locations)}
          ${intentPreviewSection('Plot Threads', intent.plotThreads || intent.plot_threads)}
          ${intentPreviewSection('Foreshadowing to Advance', intent.foreshadowingToAdvance || intent.foreshadowing_to_advance)}
          ${intentPreviewSection('Preconditions', intent.preconditions)}
          ${intentPreviewSection('Required Outcomes', intent.requiredOutcomes || intent.required_outcomes)}
        </div>
        <label class="sf-intent-field">Generation guidance <textarea name="context" rows="4" placeholder="可选：补充本次写作的上下文约束；仅在排队生成章节时传给现有 write-next 任务。"></textarea></label>
        <div class="sf-intent-preview-actions"><button type="button" class="btn btn-ghost" data-sf-intent-cancel>取消</button><span></span><button type="button" class="btn btn-secondary" data-sf-intent-save>保存为计划</button><button type="submit" class="btn btn-primary">${mode === 'generate' ? '保存并生成下一章' : '确认保存计划'}</button></div>
      </form>`, true);

    const form = document.getElementById('sf-intent-preview-form');
    form?.querySelector('[data-sf-intent-cancel]')?.addEventListener('click', () => closeModal());
    form?.querySelector('[data-sf-intent-save]')?.addEventListener('click', () => commitChapterIntentPreview('save', form, nodeIds));
    form?.addEventListener('submit', (event) => {
      event.preventDefault();
      commitChapterIntentPreview(mode, form, nodeIds);
    });
  }

  async function commitChapterIntentPreview(action, form, nodeIds) {
    if (!form) return;
    const chapterNumber = Number(form.elements.chapterNumber?.value || 0) || null;
    const context = String(form.elements.context?.value || '').trim();
    const buttons = [...form.querySelectorAll('button')];
    buttons.forEach((button) => { button.disabled = true; });
    try {
      const revision = await planningRevision();
      const result = action === 'generate'
        ? await api('POST', `/books/${currentBook()}/story-graph/planning/generate`, { nodeIds, chapterNumber, context, expectedRevision: revision })
        : await api('POST', `/books/${currentBook()}/story-graph/planning/intent`, { nodeIds, chapterNumber, save: true, expectedRevision: revision });
      closeModal();
      const planId = result.planningNode?.id;
      state.selected = planId ? new Set([planId]) : new Set(nodeIds);
      state.focus = planId || state.focus;
      state.detail = null;
      if (action === 'generate') {
        state.generationTaskId = result.taskId;
        if (window.modelWork && typeof window.modelWork.attachTask === 'function') {
          window.modelWork.attachTask(result.taskId, 'StoryFlow 章节写作', { id: result.taskId, status: result.status, type: 'write-next', data: { chapter_number: result.chapter } });
        }
      }
      await loadGraph();
      if (action === 'generate') {
        toast(`第 ${result.chapter} 章已进入现有写作任务队列：${result.taskId}。完成后由 StoryCommit 更新 Story Graph。`, 'success');
        watchGenerationTask(result.taskId, 0);
      } else {
        toast('已将结构化 Flow 保存为 PLANNED 章节计划；尚未写入 Canon。', 'success');
      }
    } catch (error) {
      buttons.forEach((button) => { button.disabled = false; });
      toast(`${action === 'generate' ? 'StoryFlow 生成章节' : '章节计划保存'}失败：${error.message}`, 'error');
    }
  }

  async function generateIntentFromSelection() {
    openChapterIntentPreview('save');
  }

  async function generateChapterFromSelection() {
    openChapterIntentPreview('generate');
  }

  async function watchGenerationTask(taskId, attempt) {
    if (!state || state.generationTaskId !== taskId) return;
    try {
      const task = await api('GET', `/tasks/${encodeURIComponent(taskId)}`);
      if (window.modelWork && typeof window.modelWork.attachTask === 'function') {
        window.modelWork.attachTask(taskId, 'StoryFlow 章节写作', task);
      }
      if (task.status === 'completed') {
        state.generationTaskId = null;
        await loadGraph();
        const completedChapter = task.chapterNumber || task.data?.chapter_number || task.data?.chapter || '下一';
        toast(`第 ${completedChapter} 章已完成，画布已重新读取 Canon 投影。`, 'success');
        return;
      }
      if (['failed', 'cancelled', 'needs_author_decision'].includes(task.status)) {
        state.generationTaskId = null;
        toast(`StoryFlow 写作任务${task.status}：${task.error || task.error_code || '请在任务中心查看详情'}`, 'error');
        return;
      }
      if (attempt >= 240) {
        toast(`StoryFlow 写作任务仍在${task.status}，请在任务中心查看，不会把未完成结果显示为 Canon。`, 'warning');
        return;
      }
      state.generationTimer = window.setTimeout(() => watchGenerationTask(taskId, attempt + 1), 1500);
    } catch (error) {
      if (attempt >= 10) {
        toast(`StoryFlow 写作任务状态读取失败：${error.message}`, 'error');
        return;
      }
      state.generationTimer = window.setTimeout(() => watchGenerationTask(taskId, attempt + 1), 1800);
    }
  }

  async function decideCandidate(decision) {
    if (!requirePlanningEditMode()) return;
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

  async function decideCandidateBranch(branchId, decision) {
    if (!requirePlanningEditMode()) return;
    const branch = candidateBranchById(branchId);
    const nodeId = branch?.rootNodeId || branch?.nodeIds?.[0];
    if (!nodeId) {
      toast('候选分支没有可决策的根节点。', 'error');
      return;
    }
    try {
      const revision = await planningRevision();
      await api('POST', `/books/${currentBook()}/story-graph/planning/decision`, {
        nodeIds: [nodeId],
        decision,
        expectedRevision: revision,
      });
      await loadGraph();
      toast(decision === 'adopt' ? '候选方案已转为 PLANNED。' : '候选方案已标记为 SUPERSEDED。', 'success');
    } catch (error) {
      toast(`候选方案决策失败：${error.message}`, 'error');
    }
  }

  async function decideCandidateSet(candidateSetId, decision) {
    if (!requirePlanningEditMode()) return;
    const candidateSet = (state.candidateSets || []).find((item) => item.candidateSetId === candidateSetId);
    const nodeIds = (candidateSet?.branches || [])
      .filter((branch) => branch.status === 'CANDIDATE')
      .map((branch) => branch.rootNodeId || branch.nodeIds?.[0])
      .filter(Boolean);
    if (!nodeIds.length) {
      toast('这个候选集合没有仍处于 CANDIDATE 的方案。', 'warning');
      return;
    }
    try {
      const revision = await planningRevision();
      await api('POST', `/books/${currentBook()}/story-graph/planning/decision`, {
        nodeIds,
        decision,
        expectedRevision: revision,
      });
      await loadGraph();
      toast(decision === 'adopt' ? '候选集合已全部纳入计划。' : '候选集合中的未决方案已全部丢弃。', 'success');
    } catch (error) {
      toast(`候选集合决策失败：${error.message}`, 'error');
    }
  }

  async function focusCandidateBranch(branchId) {
    const branch = candidateBranchById(branchId);
    if (!branch) return;
    const rootId = branch.rootNodeId || branch.nodeIds?.[0];
    if (!rootId) return;
    state.view = 'story';
    state.types = [];
    state.statuses = [];
    state.focus = rootId;
    state.depth = Math.max(2, state.depth);
    state.selected = new Set([rootId]);
    state.detail = null;
    state.edgeSelectedId = null;
    await loadGraph();
    const node = nodeById(rootId) || nodeById(branch.originNodeId);
    if (node) {
      state.selected = new Set([node.id]);
      state.focus = node.id;
      refreshNodeSelection();
      renderSidebar();
      renderInspector();
      loadNodeDetail(node.id);
      centerOn(node);
    } else {
      toast('候选方案已定位，但当前投影没有返回其根节点。请检查 planning overlay 修订。', 'warning');
    }
  }

  async function generateCandidateBranches(sourceAnalysisTaskId = '', sourceCandidate = null) {
    if (!requirePlanningEditMode()) return;
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
        sourceAnalysisTaskId: sourceAnalysisTaskId || '',
        sourceCandidateSetId: sourceCandidate?.candidateSetId || '',
        sourceCandidateBranchId: sourceCandidate?.candidateBranchId || '',
        sourceCandidateRootNodeId: sourceCandidate?.candidateRootNodeId || '',
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
        const workerImport = task.result?.candidateImport;
        if (workerImport?.status === 'completed') {
          state.candidateTaskId = null;
          state.focus = sourceNodeId || state.focus;
          state.depth = Math.max(2, state.depth);
          await loadGraph();
          toast(`候选分支已由 worker 原子写入 StoryFlow（${workerImport.createdBranchCount ?? branches.length} 个新分支，revision ${workerImport.revision}）；当前仍是 CANDIDATE，不会污染 Canon。`, 'success');
          return;
        }
        await persistCandidateBranches(
          branches,
          sourceNodeId,
          task.result?.generationRunId || '',
          task.result?.candidateSetId || '',
        );
        if (workerImport?.status === 'failed') {
          toast(`worker 已生成候选，但自动写入 planning overlay 失败，已通过幂等前端重试：${workerImport.error || 'unknown error'}`, 'warning');
        }
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

  async function persistCandidateBranches(branches, sourceNodeId, generationRunId = '', candidateSetId = '') {
    const planning = await api('GET', `/books/${currentBook()}/story-graph/planning`);
    const boundedBranches = branches.slice(0, 8);
    // Forecast workers return the authoritative task-scoped id. The fallback
    // keeps older task results importable without creating a second data
    // source, while new results never derive identity in the browser.
    const resolvedCandidateSetId = String(
      candidateSetId || `forecast:${state.candidateTaskId || generationRunId || sourceNodeId}`,
    );
    const enrichedBranches = boundedBranches.map((branch, index) => {
      if (!branch || typeof branch !== 'object') return null;
      return {
        ...branch,
        branchIndex: index + 1,
        branchCount: boundedBranches.length,
        candidateSetId: resolvedCandidateSetId,
        sourceTaskId: state.candidateTaskId,
        generationRunId,
      };
    }).filter(Boolean);
    if (!enrichedBranches.length) {
      toast('候选分支任务没有返回可持久化的对象。', 'error');
      return;
    }
    const result = await api('POST', `/books/${currentBook()}/plot-canvas/apply-candidate-set`, {
      branches: enrichedBranches,
      sourceNodeId,
      expectedRevision: Number(planning.revision || 1),
    });
    const candidateSet = result.candidateSet || {};
    const branchRoots = (candidateSet.branches || [])
      .map((branch) => branch.rootNodeId)
      .filter(Boolean);
    const persisted = Number(candidateSet.branchCount || enrichedBranches.length);
    state.candidateTaskId = null;
    state.focus = sourceNodeId;
    state.depth = Math.max(2, state.depth);
    state.selected = new Set(branchRoots);
    await loadGraph();
    toast(`已原子写入 ${persisted} 个模型返回分支（revision ${result.revision}）；当前仍是 CANDIDATE，不会污染 Canon。`, 'success');
  }

  async function recoverForecastTask(taskId, sourceNodeId = '') {
    if (!requirePlanningEditMode() || !taskId || state.recoveringForecastTaskId) return;
    state.recoveringForecastTaskId = taskId;
    renderSidebar();
    try {
      const revision = await planningRevision();
      const result = await api('POST', `/books/${currentBook()}/story-graph/candidates/recoverable-tasks/${encodeURIComponent(taskId)}/import`, {
        sourceNodeId,
        expectedRevision: revision,
      });
      state.recoveringForecastTaskId = null;
      state.focus = sourceNodeId || state.focus;
      state.depth = Math.max(2, state.depth);
      await loadGraph();
      toast(`Recovered candidate overlay: ${result.candidateSet?.createdBranchCount ?? 0} new branches. Canon was not modified.`, 'success');
    } catch (error) {
      state.recoveringForecastTaskId = null;
      renderSidebar();
      toast(`Forecast candidate recovery failed: ${error.message}`, 'error');
    }
  }

  async function analyzeSelection() {
    const nodes = analysisNodes();
    if (!nodes.length) {
      toast('先选择至少一个真实 Story Graph 节点，再运行 AI 分析；Activity Cluster 仅用于展示聚合。', 'warning');
      return;
    }
    try {
      const queued = await api('POST', `/books/${currentBook()}/story-graph/actions/analyze`, {
        nodeIds: nodes.map((node) => node.id),
        analysisTypes: ['pace', 'relationship_changes', 'logic_conflicts', 'stale_plot_threads', 'foreshadowing_progress', 'timeline_anomalies', 'repetition', 'next_steps'],
      });
      state.analysisTaskId = queued.taskId;
      state.analysisResult = null;
      state.analysisTrace = null;
      state.generationContextGraph = null;
      state.analysisHistoryLoaded = false;
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
        state.analysisTrace = result.generationRun || null;
        renderInspector();
        state.analysisHistoryLoaded = false;
        loadAnalysisHistory();
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

  function focusAnalysisEvidence(nodeId) {
    const id = String(nodeId || '').trim();
    if (!id || !state) return;
    const knownNode = nodeById(id);
    state.view = TYPE_VIEW[knownNode?.type] || 'story';
    state.focus = id;
    state.depth = 1;
    state.selected = new Set([id]);
    state.edgeSelectedId = null;
    state.detail = null;
    state.impact = null;
    state.chapterImpact = null;
    state.chapterVersionCompare = null;
    state.history = null;
    state.snapshotDiff = null;
    state.canonicalReplay = null;
    state.canonicalDiff = null;
    state.candidateComparison = null;
    state.candidateLineage = null;
    hideSearchResults();
    loadGraph();
    toast(`已定位 AI 分析证据：${id}`, 'success');
  }

  function renderAnalysisResult(inspector) {
    const result = state.analysisResult;
    if (!result || !inspector) return;
    const findings = Array.isArray(result.findings) ? result.findings : [];
    const trace = state.analysisTrace;
    const run = trace?.selectedRun;
    const runContext = run?.context;
    if (trace?.available && run) {
      const selection = (runContext?.selectionNodeIds || result.selectedNodeIds || []).join(', ') || '—';
      const contextLine = runContext?.available
        ? '<div class="sf-context-breakdown"><div class="sf-context-breakdown-row"><span>Context manifest</span><b>' + text(runContext.includedItems) + ' included / ' + text(runContext.excludedItems) + ' excluded</b><small>' + text(runContext.itemCount) + ' sources · ' + text(runContext.sourceTypes?.join(', ') || 'no source type') + ' · ' + text(runContext.exactPersistedPromptRanges) + ' exact persisted ranges</small></div></div>'
        : '<p class="dim-note">This GenerationRun has no context manifest; the UI will not infer sources from prompt text.</p>';
      inspector.insertAdjacentHTML('beforeend',
        '<div class="sf-inspector-section sf-analysis-provenance"><h4>AI action provenance</h4><div class="sf-context-banner">This report is linked to a persisted GenerationRun. The canvas shows audit metadata, not the full prompt.</div><dl class="sf-kv"><dt>GenerationRun</dt><dd>' + text(run.id || '—') + '</dd><dt>Agent</dt><dd>' + text(run.agentRole || '—') + '</dd><dt>Provider / model</dt><dd>' + text(run.provider?.name || run.provider?.id || '—') + ' / ' + text(run.model?.name || run.model?.id || '—') + '</dd><dt>Prompt</dt><dd>' + text(run.promptTokens ?? '—') + ' tokens · ' + text(run.totalTokens ?? '—') + ' total</dd><dt>Selection</dt><dd>' + text(selection) + '</dd></dl>' + contextLine + '</div>');
    } else {
      inspector.insertAdjacentHTML('beforeend', '<div class="sf-inspector-section sf-analysis-provenance"><h4>AI action provenance</h4><p class="dim-note">No GenerationRun summary is available yet. Provider-incomplete or missing audit data is not presented as model provenance.</p></div>');
    }
    const graphRunId = String(run?.id || trace?.selectedRunId || '');
    const graphSnapshot = runContext?.contextGraphSnapshot;
    const graphState = state.generationContextGraph?.runId === graphRunId ? state.generationContextGraph : null;
    if (graphState) {
      inspector.insertAdjacentHTML('beforeend', renderGenerationContextGraphSection(graphState, 'AI Context Graph'));
    } else if (graphRunId && graphSnapshot?.available) {
      inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section sf-context-graph-evidence"><h4>AI Context Graph</h4><p class="dim-note">A metadata-only Context Graph snapshot is persisted for this GenerationRun. Load it to inspect the exact source nodes and semantic edges selected for the AI action.</p><button class="btn btn-sm btn-secondary" data-sf-context-graph-load="${attr(graphRunId)}">View Context Graph</button></div>`);
    } else if (trace?.available && run) {
      inspector.insertAdjacentHTML('beforeend', '<div class="sf-inspector-section sf-context-graph-evidence"><h4>AI Context Graph</h4><p class="dim-note">No persisted Context Graph snapshot is available for this run. The UI will not infer AI context from the current Story Graph or prompt text.</p></div>');
    }
    const canGenerateBranches = !!state.editMode && modelRuntimeReady() && (result.selectedNodeIds || []).some((id) => nodeById(id));
    inspector.insertAdjacentHTML('beforeend', '<div class="sf-inspector-section sf-analysis-actions"><div class="sf-inspector-actions"><button class="btn btn-sm btn-secondary" data-sf-analysis-action="generate-candidates" ' + (canGenerateBranches ? '' : 'disabled aria-disabled="true"') + '>生成三个候选分支</button></div>' + (canGenerateBranches ? '' : '<small class="dim-note">切换到“规划编辑”后，可将本次分析选择交给候选分支任务。</small>') + '</div>');
    inspector.querySelector('[data-sf-analysis-action="generate-candidates"]')?.addEventListener('click', () => generateCandidateBranches(state.analysisTaskId || ''));
    inspector.querySelectorAll('[data-sf-context-graph-load]').forEach((button) => button.addEventListener('click', () => {
      loadGenerationRunContextGraph(button.dataset.sfContextGraphLoad || '');
    }));
    const findingRows = findings.map((finding) => {
      const evidenceIds = Array.isArray(finding.evidenceNodeIds)
        ? finding.evidenceNodeIds.map((item) => String(item || '').trim()).filter(Boolean)
        : [];
      const evidence = evidenceIds.length
        ? `<div class="sf-analysis-evidence"><span>证据：</span>${evidenceIds.map((id) => `<button class="sf-analysis-evidence-button" data-sf-analysis-evidence="${attr(id)}">${text(id)}</button>`).join('')}</div>`
        : '';
      return `<li><span><b class="sf-severity-${text(finding.severity || 'info')}">${text(finding.kind || 'observation')}</b> ${text(finding.message)}${evidence}</span></li>`;
    }).join('');
    inspector.insertAdjacentHTML('beforeend', `<div class="sf-inspector-section sf-analysis-result"><h4>AI 分析结果 · ${text(result.source || 'model')}</h4><div class="sf-context-banner">${text(result.summary || '模型没有返回摘要。')}</div><ul class="sf-inspector-list">${findingRows || '<li>模型没有返回结构化 findings。</li>'}</ul>${Array.isArray(result.nextSteps) && result.nextSteps.length ? `<h4 style="margin-top:10px">下一步</h4><ul class="sf-inspector-list">${result.nextSteps.map((item) => `<li>${text(item)}</li>`).join('')}</ul>` : ''}</div>`);
    inspector.querySelectorAll('[data-sf-analysis-evidence]').forEach((button) => button.addEventListener('click', () => focusAnalysisEvidence(button.dataset.sfAnalysisEvidence || '')));
  }

  async function loadAnalysisHistory() {
    if (!state || !S.book) return;
    try {
      const payload = await api('GET', '/books/' + currentBook() + '/story-graph/actions/analyze?limit=12');
      if (!state) return;
      state.analysisHistory = Array.isArray(payload.tasks) ? payload.tasks : [];
      state.analysisHistoryLoaded = true;
      renderSidebar();
      renderInspector();
    } catch (error) {
      if (state) state.analysisHistoryLoaded = false;
      toast('AI 分析历史读取失败：' + error.message, 'warning');
    }
  }

  function restoreAnalysisTask(taskId) {
    const task = (state.analysisHistory || []).find((item) => item.taskId === taskId);
    if (!task) return;
    const ids = (task.nodeIds || []).filter((id) => nodeById(id));
    if (!ids.length) {
      toast('该分析报告引用的节点不在当前焦点子图中；请先切换到对应视图或搜索节点。', 'warning');
      return;
    }
    state.selected = new Set(ids);
    state.focus = ids[0];
    state.analysisTaskId = task.taskId;
    state.analysisResult = task.status === 'completed' && task.result ? task.result : null;
    state.analysisTrace = task.generationRun || null;
    state.generationContextGraph = null;
    state.snapshotDiff = null;
    state.edgeSelectedId = null;
    refreshNodeSelection();
    renderSidebar();
    renderInspector();
    const node = nodeById(ids[0]);
    if (node) centerOn(node);
    if (task.status !== 'completed') watchAnalysisTask(task.taskId, 0);
  }

  async function loadLayoutHistory(render = true) {
    if (!state?.view || !S.book) return;
    try {
      state.layoutHistory = await api('GET', `/books/${currentBook()}/story-graph/layout/history?view=${encodeURIComponent(state.view)}`);
    } catch (error) {
      state.layoutHistory = { view: state.view, headRevision: 0, latestRevision: 0, canUndo: false, canRedo: false, entries: [], error: error.message };
    }
    if (render) renderToolbar();
  }

  async function saveLayout() {
    if (!state?.graph) return;
    const items = state.graph.nodes.map((node) => ({ nodeId: node.id, x: node.x, y: node.y, collapsed: !!node.collapsed, pinned: !!node.pinned, hidden: !!node.hidden }));
    try {
      const result = await api('POST', `/books/${currentBook()}/story-graph/layout`, { view: state.view, items });
      state.layoutHistory = result.history || state.layoutHistory;
      state.layoutDirty = false;
      renderToolbar();
      toast('StoryFlow 工作区布局已保存。', 'success');
    } catch (error) {
      toast(`布局保存失败：${error.message}`, 'error');
    }
  }

  async function moveLayoutHistory(direction) {
    if (!state?.graph) return;
    if (state.layoutDirty && !window.confirm('当前画布有未保存的位置变化；继续将丢弃这些变化吗？')) return;
    try {
      const result = await api('POST', `/books/${currentBook()}/story-graph/layout/${direction}`, { view: state.view });
      state.layoutHistory = result.history || state.layoutHistory;
      state.layoutDirty = false;
      await loadGraph();
      toast(direction === 'undo' ? '已撤销上一次布局保存。' : '已恢复下一次布局保存。', 'success');
    } catch (error) {
      toast(`${direction === 'undo' ? '撤销' : '重做'}布局失败：${error.message}`, 'warning');
    }
  }

  function undoLayout() { return moveLayoutHistory('undo'); }

  function redoLayout() { return moveLayoutHistory('redo'); }

  async function autoLayout() {
    if (!state?.graph) return;
    try {
      const result = await api('POST', `/books/${currentBook()}/story-graph/layout/auto`, { view: state.view, focus: state.focus || null, depth: state.depth, items: [] });
      const positions = new Map((result.items || []).map((item) => [item.nodeId, item]));
      state.graph.nodes.forEach((node) => {
        const position = positions.get(node.id);
        if (position) { node.x = position.x; node.y = position.y; node.collapsed = !!position.collapsed; node.pinned = !!position.pinned; node.hidden = !!position.hidden; }
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
    window.clearTimeout(state?.generationTimer);
    window.clearTimeout(state?.viewportFetchTimer);
    window.clearInterval(state?.graphFreshnessTimer);
    if (state?.connection) stopPortDrag();
    state?.modelWorkObserver?.disconnect();
    state?.canvasObserver?.disconnect();
    hideContextMenu();
  }

  PAGES.storyflow = async function storyflowPage(page) {
    if (state) destroy();
    const routeIntent = window.__storyflowRouteIntent || {};
    window.__storyflowRouteIntent = null;
    state = {
      view: VIEW_META[routeIntent.view] ? routeIntent.view : 'story', depth: 1, focus: routeIntent.focus || '', types: [], statuses: [], chapterFrom: '', chapterTo: '', volumeNumber: '', timeFrom: '', timeTo: '', plotThread: '',
      graph: null, graphSnapshotId: '', graphFreshness: null, graphFreshnessError: null, graphFreshnessLoading: false, graphFreshnessTimer: null, modelReadiness: null, modelReadinessLoading: false, modelReadinessRequestId: 0, selected: new Set(), edgeSelectedId: null, edgeHoveredId: null, detail: null, impact: null, history: null, layoutHistory: { view: 'story', headRevision: 0, latestRevision: 0, canUndo: false, canRedo: false, entries: [] }, context: null, contextChapterId: '', contextError: null, neighborLoading: false, transform: { tx: 0, ty: 0, scale: 1 }, minimapDrag: null, presentationMode: 'clustered', expandedPresentationClusters: new Set(), presentationClusterPositions: {}, viewportFetchTimer: null, viewportLastKey: '', viewportRequestKey: '', viewportFetchInFlight: false, viewportEdgeFetchInFlight: false, boundaryFetchInFlight: false, viewportFetchError: null, viewportPages: new Set(), viewportContinuation: null, viewportEdgeContinuation: null, viewportEdgeExhausted: false,
      editMode: false,
      contextEvidence: null, generationRunTrace: null, generationContextGraph: null, reconciliationCandidates: null, selectionProjection: null, drag: null, pan: null, box: null, connection: null, layoutDirty: false, searchTimer: null, candidateTimer: null, candidateTaskId: null, analysisTimer: null, analysisTaskId: null, analysisResult: null, analysisTrace: null, generationTimer: null, generationTaskId: null, chapterImpact: null, chapterVersionCompare: null, modelWorkObserver: null,
      analysisHistory: [], analysisHistoryLoaded: false, candidateSets: [], candidateSetsRevision: 0, candidateSetsLoading: false, candidateSetsError: null, recoverableForecastTasks: [], recoverableForecastTasksLoading: false, recoverableForecastTasksError: null, recoveringForecastTaskId: null, candidateComparison: null, candidateLineage: null, snapshotDiff: null, canonicalReplay: null, canonicalDiff: null,
      storyHealth: null, storyHealthLoading: false, storyHealthError: null, storyHealthLookback: 8, storyHealthRequestId: 0,
      graphFreshnessDisabled: false,
    };
    if (state.focus) state.selected = new Set([state.focus]);
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
    loadModelReadiness();
    await loadGraph();
    startGraphFreshnessMonitor();
  };

  window.storyflow = {
    destroy,
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
  window.openStoryFlowView = openStoryFlowView;
  if (typeof S !== 'undefined' && S.book) {
    const legacyRouteViews = typeof STORYFLOW_COMPAT_ROUTES !== 'undefined'
      ? STORYFLOW_COMPAT_ROUTES
      : {
        mindmap: 'story',
        flow: 'story',
        timeline: 'timeline',
        plot: 'story',
        'world-map': 'world',
        foreshadowing: 'foreshadow',
        characters: 'character',
      };
    const existingIntent = window.__storyflowRouteIntent || {};
    if (S.page === 'storyflow' || legacyRouteViews[S.page]) {
      window.__storyflowRouteIntent = {
        view: existingIntent.view || legacyRouteViews[S.page] || 'story',
        focus: existingIntent.focus || '',
        sourcePage: existingIntent.sourcePage || (legacyRouteViews[S.page] ? S.page : ''),
      };
      // The Workbench loader now starts this module before the shell and lets
      // the shell own the first render. Calling the legacy router here would
      // race that deep-link render and could put Dashboard back over Canvas.
      window.__storyflowRouteIntentPending = true;
    }
  }
}());

// The StoryFlow module is loaded after the base Studio navigation. Re-render
// the navigation once the route becomes available so a first page load does
// not hide the entry until the next refresh.
if (typeof renderNav === 'function') renderNav();
