/* global S, PAGES, NAV, persistNavState, setActiveBook, esc */

/*
 * NovelForge Studio Application Shell
 *
 * The shell is a deliberately small orchestration module. Page implementations
 * remain adapters around the existing API-backed functions; this module owns
 * only workspace identity, URL state, lifecycle, panels, layout preferences,
 * keyboard commands and responsive chrome.
 */
(function () {
  'use strict';

  if (window.__novelforgeStudioShell) return;
  window.__novelforgeStudioShell = true;

  const STORAGE_KEY = 'novelforge-workbench-layout-v1';
  const MORE_KEY = 'novelforge-workbench-more-open';
  const FOCUS_KEY = 'novelforge-workbench-focus';
  const DENSITIES = Object.freeze({ compact: 1440, standard: 2200 });
  const pageLabels = Object.freeze({
    dashboard: 'Dashboard',
    book: 'Project Overview',
    chapters: 'Write',
    planning: 'Plan',
    wizard: 'Canon',
    storyflow: 'StoryFlow',
    jointreview: 'Review',
    timeline: 'Timeline',
    chat: 'AI Assistant',
    'agent-config': 'AI Runtime',
    tasks: 'Tasks',
    import: 'Import / Export',
    settings: 'Settings',
    doctor: 'Diagnostics',
    genres: 'Genres',
    simulation: 'Simulation',
  });

  const workspaceMeta = Object.freeze([
    { id: 'write', label: 'Write 写作', short: '写', page: 'chapters', symbol: '✎' },
    { id: 'plan', label: 'Plan 规划', short: '规', page: 'planning', symbol: '⌁' },
    { id: 'storyflow', label: 'StoryFlow', short: '流', page: 'storyflow', symbol: '◇' },
    { id: 'canon', label: 'Canon 世界观', short: '典', page: 'wizard', symbol: '▣' },
    { id: 'review', label: 'Review 审查', short: '审', page: 'jointreview', symbol: '✓' },
    { id: 'timeline', label: 'Timeline 时间线', short: '时', page: 'storyflow', symbol: '◷', storyView: 'timeline' },
  ]);

  const moreMeta = Object.freeze([
    { id: 'chat', label: 'AI Assistant', page: 'chat', symbol: '✦' },
    { id: 'agent-config', label: 'AI Runtime', page: 'agent-config', symbol: '⚙' },
    { id: 'tasks', label: 'Tasks', page: 'tasks', symbol: '☷' },
    { id: 'simulation', label: 'Simulation', page: 'simulation', symbol: '◎' },
    { id: 'import', label: 'Import / Export', page: 'import', symbol: '↕' },
    { id: 'settings', label: 'Settings', page: 'settings', symbol: '⚙' },
    { id: 'doctor', label: 'Diagnostics', page: 'doctor', symbol: '⌁' },
    { id: 'genres', label: 'Genres', page: 'genres', symbol: '▤' },
    { id: 'continuous', label: 'Continuous Writing', page: 'continuous', symbol: '▶' },
    { id: 'forecast', label: 'Forecast', page: 'forecast', symbol: '◉' },
    { id: 'export', label: 'Delivery', page: 'export', symbol: '⇩' },
  ]);

  const legacyToRoute = Object.freeze({
    dashboard: { id: 'dashboard', page: 'dashboard' },
    book: { id: 'overview', page: 'book' },
    chapters: { id: 'write', page: 'chapters' },
    write: { id: 'write', page: 'chapters' },
    planning: { id: 'plan', page: 'planning' },
    plan: { id: 'plan', page: 'planning' },
    wizard: { id: 'canon', page: 'wizard' },
    canon: { id: 'canon', page: 'wizard' },
    storyflow: { id: 'storyflow', page: 'storyflow' },
    review: { id: 'review', page: 'jointreview' },
    jointreview: { id: 'review', page: 'jointreview' },
    timeline: { id: 'timeline', page: 'storyflow', storyView: 'timeline' },
    mindmap: { id: 'storyflow', page: 'storyflow', storyView: 'story' },
    flow: { id: 'storyflow', page: 'storyflow', storyView: 'story' },
    plot: { id: 'storyflow', page: 'storyflow', storyView: 'story' },
    'world-map': { id: 'storyflow', page: 'storyflow', storyView: 'world' },
    foreshadowing: { id: 'storyflow', page: 'storyflow', storyView: 'foreshadow' },
    characters: { id: 'storyflow', page: 'storyflow', storyView: 'character' },
  });

  const morePages = new Set(moreMeta.map((item) => item.page));
  const legacyRender = window.render;
  const legacyGo = window.go;
  const legacyRenderNav = window.renderNav;
  let initialized = false;
  let activeRoute = null;
  let activePage = null;
  let moreOpen = false;
  let layoutState = readLayoutState();
  let commandBackdrop = null;
  let commandSelection = 0;
  let renderSerial = 0;
  let renderController = null;
  let workspaceController = null;

  const registry = new Map();

  function qs(selector, root = document) {
    return root.querySelector(selector);
  }

  function qsa(selector, root = document) {
    return Array.from(root.querySelectorAll(selector));
  }

  function safeStorageGet(key, fallback) {
    try {
      const value = window.localStorage.getItem(key);
      return value === null ? fallback : value;
    } catch (_) {
      return fallback;
    }
  }

  function safeStorageSet(key, value) {
    try { window.localStorage.setItem(key, value); } catch (_) { /* private mode */ }
  }

  function readLayoutState() {
    try {
      const raw = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}');
      return raw && typeof raw === 'object' ? raw : {};
    } catch (_) {
      return {};
    }
  }

  function writeLayoutState() {
    safeStorageSet(STORAGE_KEY, JSON.stringify(layoutState));
  }

  function beginWorkspaceRequestScope() {
    if (workspaceController) {
      workspaceController.abort();
      // Legacy chapter editors and other modal adapters own timers outside
      // #page.  Navigation must close that surface as part of the same
      // workspace boundary so autosave/listeners cannot outlive the page.
      if (typeof window.closeModal === 'function') window.closeModal();
    }
    workspaceController = new AbortController();
    window.__studioWorkspaceSignal = workspaceController.signal;
  }

  function currentDensity() {
    const width = Math.max(0, Number(window.innerWidth) || 0);
    if (width < DENSITIES.compact) return 'compact';
    if (width < DENSITIES.standard) return 'standard';
    return 'expanded';
  }

  function defaultLayout(workspaceId) {
    const compact = currentDensity() === 'compact';
    return {
      workspace: workspaceId,
      density: currentDensity(),
      explorer: !compact,
      inspector: !compact,
      bottom: false,
      bottomTab: workspaceId === 'storyflow' ? 'timeline' : 'activity',
      bottomHeight: 224,
      focus: false,
      explorerWidth: 216,
      inspectorWidth: 296,
    };
  }

  function getLayout(workspaceId) {
    const existing = layoutState[workspaceId];
    if (existing && typeof existing === 'object') {
      const layout = Object.assign(defaultLayout(workspaceId), existing);
      // Layout preferences created before density-aware state existed may
      // have both side panels open.  The first compact render must still
      // honour information reduction instead of opening two drawers over the
      // main workspace.
      if (currentDensity() === 'compact' && existing.density !== 'compact') {
        layout.density = 'compact';
        layout.explorer = false;
        layout.inspector = false;
        layout.bottom = false;
        layoutState[workspaceId] = layout;
        writeLayoutState();
      }
      return layout;
    }
    const initial = defaultLayout(workspaceId);
    layoutState[workspaceId] = initial;
    writeLayoutState();
    return initial;
  }

  function setLayout(workspaceId, patch) {
    layoutState[workspaceId] = Object.assign(getLayout(workspaceId), patch);
    writeLayoutState();
    applyShellState();
  }

  function workspaceForPage(page) {
    if (legacyToRoute[page]) return legacyToRoute[page];
    const more = moreMeta.find((item) => item.page === page);
    return more ? { id: 'more', page: more.page, morePage: more.page } : null;
  }

  function internalPage(meta) {
    if (meta.page === 'planning' && typeof PAGES.planning !== 'function') return 'wizard';
    if (meta.page === 'storyflow' && typeof PAGES.storyflow !== 'function') return 'timeline';
    return meta.page;
  }

  function resolveRoute(route) {
    const value = String(route || '').replace(/^\//, '');
    if (legacyToRoute[value]) {
      const meta = Object.assign({}, legacyToRoute[value]);
      if (meta.id === 'storyflow' && value === 'storyflow') meta.storyView = 'story';
      return meta;
    }
    const workspace = workspaceMeta.find((item) => item.id === value);
    if (workspace) return Object.assign({}, workspace);
    const more = moreMeta.find((item) => item.id === value || item.page === value);
    if (more) return { id: 'more', page: more.page, morePage: more.page };
    return null;
  }

  function routePath(meta) {
    if (!meta || meta.id === 'dashboard' || !S.book) return '/';
    const book = encodeURIComponent(S.book);
    if (meta.id === 'overview') return `/project/${book}`;
    if (meta.id === 'more') return `/project/${book}/more/${encodeURIComponent(meta.morePage || meta.page)}`;
    return `/project/${book}/${encodeURIComponent(meta.id)}`;
  }

  function parseLocation() {
    const parts = window.location.pathname.split('/').filter(Boolean).map((part) => {
      try { return decodeURIComponent(part); } catch (_) { return part; }
    });
    if (parts[0] !== 'project' || !parts[1]) return null;
    const bookId = parts[1];
    if (!parts[2]) return { bookId, meta: { id: 'overview', page: 'book' } };
    if (parts[2] === 'more') {
      const morePage = parts[3] || 'tasks';
      return { bookId, meta: resolveRoute(morePage) || { id: 'more', page: morePage, morePage } };
    }
    return { bookId, meta: resolveRoute(parts[2]) || { id: 'overview', page: 'book' } };
  }

  function changeUrl(meta, replace) {
    const path = routePath(meta);
    if (window.location.pathname === path && !window.location.search) return;
    const method = replace ? 'replaceState' : 'pushState';
    window.history[method]({ studioWorkspace: meta.id, projectId: S.book || null }, '', path);
  }

  function setStoryFlowIntent(meta, focus) {
    if (meta.page !== 'storyflow') return;
    window.__storyflowRouteIntent = {
      view: meta.storyView || 'story',
      focus: focus || '',
      sourcePage: meta.id,
    };
  }

  function registerWorkspace(id, definition) {
    registry.set(id, Object.assign({
      id,
      mount() {},
      activate() {},
      deactivate() {},
      unmount() {},
    }, definition));
  }

  function registerBuiltIns() {
    registerWorkspace('dashboard', { label: 'Dashboard', page: 'dashboard' });
    registerWorkspace('overview', { label: 'Project Overview', page: 'book' });
    workspaceMeta.forEach((item) => registerWorkspace(item.id, item));
    registerWorkspace('more', { label: 'More', page: 'more' });
  }

  function deactivateWorkspace(nextId) {
    if (!activeRoute || activeRoute.id === nextId) return;
    window.dispatchEvent(new CustomEvent('studio-workspace-deactivating', {
      detail: { from: Object.assign({}, activeRoute), to: nextId },
    }));
    const previous = registry.get(activeRoute.id);
    if (previous && typeof previous.deactivate === 'function') previous.deactivate({ from: activeRoute });
    if (activeRoute.id === 'storyflow' || activePage === 'storyflow') {
      if (window.storyflow && typeof window.storyflow.destroy === 'function') window.storyflow.destroy();
    }
  }

  function activateWorkspace(meta) {
    const entry = registry.get(meta.id) || registry.get('more');
    activeRoute = Object.assign({}, meta);
    activePage = internalPage(meta);
    if (entry && typeof entry.activate === 'function') entry.activate({ route: activeRoute, page: activePage });
  }

  function navigate(route, options = {}) {
    const meta = typeof route === 'string' ? resolveRoute(route) : route;
    if (!meta) return legacyGo(route);
    if (meta.id !== 'dashboard' && !S.book) {
      return legacyGo('dashboard');
    }

    const page = internalPage(meta);
    beginWorkspaceRequestScope();
    deactivateWorkspace(meta.id);
    if (meta.id === 'more') moreOpen = true;
    setStoryFlowIntent(meta, options.focus || '');
    activePage = page;
    activeRoute = Object.assign({}, meta);
    S.page = page;
    if (S.book) {
      try { window.localStorage.setItem('novelforge-active-page', page); } catch (_) {}
      if (typeof persistNavState === 'function') persistNavState();
    }
    activateWorkspace(meta);
    if (options.fromLocation) changeUrl(meta, true);
    else if (options.history !== false) changeUrl(meta, Boolean(options.replace));
    closeCompactExplorer();
    renderNavigation();
    const result = window.render();
    return result;
  }

  function renderNavigation() {
    if (typeof legacyRenderNav === 'function') legacyRenderNav();
    if (initialized) syncNavigation();
  }

  function routeButton(meta, extraClass = '') {
    const current = activeRoute && (activeRoute.id === meta.id || meta.id === 'more' && activeRoute.id === 'more' && activeRoute.page === meta.page);
    const label = meta.label || pageLabels[meta.page] || meta.page;
    const symbol = meta.symbol || '·';
    return `<button type="button" class="studio-explorer-route ${extraClass}" data-shell-route="${meta.id}" aria-current="${current ? 'page' : 'false'}" title="${label}">` +
      `<span class="studio-explorer-route-icon" aria-hidden="true">${symbol}</span><span>${label}</span></button>`;
  }

  function explorerMarkup() {
    const project = S.books.find((book) => book.id === S.book);
    const projectTitle = project && project.title ? project.title : (S.book ? '当前作品' : '未选择作品');
    const projectMark = String(projectTitle).trim().slice(0, 1) || '—';
    const primary = S.book
      ? `<div class="studio-explorer-group"><div class="studio-explorer-group-label">Project</div>${routeButton({ id: 'overview', label: 'Project Overview', page: 'book', symbol: '⌂' })}</div>`
      : '<div class="studio-explorer-empty">从 Dashboard 打开一本作品后，Write、Plan、StoryFlow、Canon、Review 和 Timeline 会出现在这里。</div>';
    const workspaces = S.book
      ? `<div class="studio-explorer-group"><div class="studio-explorer-group-label">Workspaces</div>${workspaceMeta.map((item) => routeButton(item)).join('')}</div>`
      : '';
    const moreItems = S.book ? moreMeta.map((item) => routeButton({ id: item.page, label: item.label, page: item.page, morePage: item.page, symbol: item.symbol }, 'studio-more-route')).join('') : '';
    return `<div class="studio-explorer-head">
      <span class="studio-explorer-eyebrow">Project Explorer</span>
      <button type="button" class="studio-explorer-project" data-shell-project-switcher title="切换作品">
        <span class="studio-explorer-project-mark" aria-hidden="true">${esc(projectMark)}</span>
        <span class="studio-explorer-project-name">${esc(projectTitle)}</span>
      </button>
    </div>
    ${primary}${workspaces}
    <div class="studio-explorer-group studio-explorer-more">
      <button type="button" class="studio-explorer-more-toggle" data-shell-more-toggle aria-expanded="${moreOpen ? 'true' : 'false'}">
        <span><span aria-hidden="true">＋</span><span>More 辅助功能</span></span><span aria-hidden="true">${moreOpen ? '−' : '＋'}</span>
      </button>
      <div class="studio-explorer-more-list" data-shell-more-list ${moreOpen ? '' : 'hidden'}>${moreItems || '<div class="studio-explorer-empty">打开作品后可用</div>'}</div>
    </div>`;
  }

  function activityMarkup() {
    const items = [{ id: 'dashboard', label: 'Home', symbol: '⌂' }].concat(S.book ? workspaceMeta : []);
    return items.map((item) => {
      const current = activeRoute && activeRoute.id === item.id;
      const label = item.short || item.label;
      return `<button type="button" class="studio-activity-button" data-shell-route="${item.id}" aria-current="${current ? 'page' : 'false'}" title="${item.label}">` +
        `<span aria-hidden="true">${item.symbol || '·'}</span><small>${label}</small></button>`;
    }).join('') + (S.book ? '<span class="studio-activity-divider" aria-hidden="true"></span><button type="button" class="studio-activity-button" data-shell-action="more" aria-current="false" title="More 辅助功能"><span aria-hidden="true">⋯</span><small>More</small></button>' : '');
  }

  function syncNavigation() {
    const nav = qs('#nav');
    const activity = qs('#studio-activity-nav');
    const shell = qs('#studio-shell');
    if (nav) nav.innerHTML = explorerMarkup();
    if (activity) activity.innerHTML = activityMarkup();
    if (shell) {
      const route = activeRoute || workspaceForPage(S.page) || { id: S.book ? 'overview' : 'dashboard', page: S.page };
      shell.dataset.workspace = route.id;
      shell.dataset.routePage = S.page || '';
      shell.dataset.density = currentDensity();
      shell.dataset.explorer = getLayout(route.id).explorer ? 'open' : 'closed';
      updateGlobalBar(route);
    }
    applyShellState();
  }

  function updateGlobalBar(meta) {
    const project = S.books.find((book) => book.id === S.book);
    const title = project && project.title ? project.title : (S.book ? '当前作品' : '未选择作品');
    const mark = qs('.studio-global-project-mark');
    const name = qs('.studio-global-project-name');
    const route = qs('#studio-global-route');
    if (mark) mark.textContent = String(title).trim().slice(0, 1) || '—';
    if (name) name.textContent = title;
    if (route) route.textContent = meta && meta.id === 'more' ? `More / ${pageLabels[meta.page] || meta.page}` : (meta && meta.label) || pageLabels[S.page] || 'Dashboard';
  }

  function setOuterInspector(open) {
    const shell = qs('#studio-shell');
    const panel = qs('#studio-shell-inspector');
    const button = qs('[data-shell-action="outer-inspector"]');
    if (!shell || !panel) return;
    shell.dataset.outerInspector = open ? 'open' : 'closed';
    panel.hidden = !open;
    panel.innerHTML = open ? `<div class="studio-shell-inspector-inner">
      <div><span class="studio-explorer-eyebrow">Workspace Inspector</span><h3>${esc(pageLabels[S.page] || S.page || 'Dashboard')}</h3></div>
      <p>这是 Shell 级上下文面板。StoryFlow 的节点 Inspector 仍由其业务工作区管理；这里保留项目、工作区和布局偏好，不写入 Canon。</p>
      <div class="kv"><span>Project</span><b>${esc(S.book || '—')}</b></div>
      <div class="kv"><span>Route</span><b>${esc(window.location.pathname)}</b></div>
      <div class="kv"><span>Density</span><b>${esc(currentDensity())}</b></div>
      <button class="btn btn-sm btn-secondary" type="button" data-shell-action="panels">打开 Bottom Panel</button>
    </div>` : '';
    if (button) button.setAttribute('aria-pressed', open ? 'true' : 'false');
  }

  function closeCompactInspector() {
    if (currentDensity() !== 'compact') return;
    const route = activeRoute || workspaceForPage(S.page);
    if (route?.id === 'storyflow' || S.page === 'storyflow') {
      const layout = getLayout(route?.id || 'storyflow');
      if (layout.inspector) setLayout(route?.id || 'storyflow', { inspector: false });
      return;
    }
    const shell = qs('#studio-shell');
    if (shell?.dataset.outerInspector === 'open') {
      setOuterInspector(false);
      if (route) setLayout(route.id, { inspector: false });
    }
  }

  function compactDrawerFocusables(drawer) {
    if (!drawer) return [];
    return qsa('button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])', drawer)
      .filter((element) => element.offsetParent !== null);
  }

  function focusCompactDrawer() {
    if (currentDensity() !== 'compact') return;
    const shell = qs('#studio-shell');
    const route = activeRoute || workspaceForPage(S.page);
    const drawer = shell?.dataset.explorer === 'open'
      ? qs('.studio-explorer')
      : route?.id === 'storyflow' && getLayout(route.id).inspector
        ? qs('.storyflow-inspector')
        : shell?.dataset.outerInspector === 'open'
          ? qs('.studio-shell-inspector')
          : null;
    const first = compactDrawerFocusables(drawer)[0];
    if (first) window.requestAnimationFrame(() => first.focus({ preventScroll: true }));
  }

  function renderBottomPanel(layout) {
    const panel = qs('#studio-bottom-panel');
    if (!panel) return;
    if (!layout.bottom) {
      panel.hidden = true;
      panel.innerHTML = '';
      return;
    }
    const tabs = activeRoute && activeRoute.id === 'storyflow'
      ? [['timeline', 'Timeline'], ['simulation', 'Simulation'], ['event-log', 'Event Log'], ['runs', 'Runs'], ['problems', 'Problems']]
      : [['activity', 'Activity'], ['event-log', 'Event Log'], ['problems', 'Problems']];
    const selected = tabs.some(([id]) => id === layout.bottomTab) ? layout.bottomTab : tabs[0][0];
    panel.hidden = false;
    panel.style.setProperty('--shell-bottom-height', `${Math.max(144, Math.min(520, Number(layout.bottomHeight) || 224))}px`);
    panel.innerHTML = `<div class="studio-bottom-tabs" role="tablist" aria-label="Bottom Panel tabs">
      <button type="button" class="studio-bottom-resize" data-shell-resize="bottom" aria-label="调整 Bottom Panel 高度" title="拖动调整高度">↕</button>
      ${tabs.map(([id, label]) => `<button type="button" class="studio-bottom-tab" data-shell-bottom-tab="${id}" role="tab" aria-selected="${id === selected ? 'true' : 'false'}">${label}</button>`).join('')}
      <button type="button" class="studio-global-action studio-bottom-close" data-shell-action="close-bottom" title="关闭 Bottom Panel">关闭</button>
    </div><div class="studio-bottom-content" data-shell-bottom-content>${bottomContent(selected)}</div>`;
    installBottomResize(panel);
  }

  function bottomContent(tab) {
    const workspace = pageLabels[S.page] || S.page || 'Dashboard';
    if (tab === 'timeline') return `<h3>Timeline · StoryFlow 底部视图</h3><p>时间线与当前 Canvas 共享 SQLite Story Graph 投影。选择节点后可从 Inspector 查看章节证据；这里不产生新的 Canon 来源。</p><div class="row row-wrap mt8"><button class="btn btn-sm btn-secondary" type="button" data-shell-bottom-action="open-timeline">在 Canvas 中打开时间线</button></div>`;
    if (tab === 'simulation') return `<h3>Simulation</h3><p>叙事模拟运行、事件流和预算属于辅助工作区，不作为 StoryFlow Canvas 的永久右栏。</p><div class="row row-wrap mt8"><button class="btn btn-sm btn-secondary" type="button" data-shell-bottom-action="open-simulation">打开叙事模拟</button></div>`;
    if (tab === 'runs') return `<h3>Runs</h3><p>持久化 GenerationRun 与任务详情仍从 Tasks 读取；模型运行不会直接写入 StoryFact。</p><div class="row row-wrap mt8"><button class="btn btn-sm btn-secondary" type="button" data-shell-bottom-action="open-tasks">打开 Tasks</button></div>`;
    if (tab === 'problems') return `<h3>Problems</h3><p>当前页面的异常、审查门禁和作者决定应通过 Review 或任务详情处理；Shell 只显示入口，不代替业务判定。</p><div class="row row-wrap mt8"><button class="btn btn-sm btn-secondary" type="button" data-shell-bottom-action="open-review">打开 Review</button></div>`;
    if (tab === 'event-log') {
      const active = Object.values(S.activeTasks || {}).filter((task) => task && !['completed', 'failed', 'cancelled'].includes(task.status));
      return `<h3>Event Log · ${esc(workspace)}</h3>${active.length ? `<ul>${active.slice(0, 8).map((task) => `<li>${esc(task.displayName || task.type || '任务')} · ${esc(task.status || '处理中')}</li>`).join('')}</ul>` : '<p>当前没有活动中的持久化任务。</p>'}`;
    }
    return `<h3>Activity</h3><p>Bottom Panel 默认关闭。打开后用于承载当前工作区的辅助状态，不挤压主编辑或 Canvas 区域。</p>`;
  }

  function applyStoryFlowLayout(layout) {
    const body = qs('.storyflow-body');
    if (!body) return;
    body.style.setProperty('--sf-explorer-width', `${Math.max(180, Math.min(360, Number(layout.explorerWidth) || 216))}px`);
    body.style.setProperty('--sf-inspector-width', `${Math.max(240, Math.min(420, Number(layout.inspectorWidth) || 296))}px`);
    installStoryFlowResizeHandles(body);
  }

  function installStoryFlowResizeHandles(body) {
    ['explorer', 'inspector'].forEach((kind) => {
      if (qs(`[data-shell-resize="${kind}"]`, body)) return;
      const handle = document.createElement('button');
      handle.type = 'button';
      handle.className = 'shell-panel-resize';
      handle.dataset.shellResize = kind;
      handle.setAttribute('aria-label', `调整 ${kind === 'explorer' ? 'Explorer' : 'Inspector'} 宽度`);
      body.appendChild(handle);
      handle.addEventListener('pointerdown', (event) => beginResize(event, kind, body));
    });
  }

  function installBottomResize(panel) {
    const handle = qs('[data-shell-resize="bottom"]', panel);
    if (!handle || handle.dataset.bound === 'true') return;
    handle.dataset.bound = 'true';
    handle.addEventListener('pointerdown', (event) => beginBottomResize(event, panel));
  }

  function beginBottomResize(event, panel) {
    if (!panel || panel.hidden) return;
    event.preventDefault();
    const route = activeRoute || { id: 'dashboard' };
    const layout = getLayout(route.id);
    const startY = event.clientY;
    const start = Number(layout.bottomHeight) || 224;
    const move = (moveEvent) => {
      const next = Math.max(144, Math.min(520, start + startY - moveEvent.clientY));
      panel.style.setProperty('--shell-bottom-height', `${Math.round(next)}px`);
    };
    const up = () => {
      const value = Math.max(144, Math.min(520, parseFloat(getComputedStyle(panel).height) || start));
      setLayout(route.id, { bottomHeight: value });
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up, { once: true });
  }

  function beginResize(event, kind, body) {
    if (currentDensity() === 'compact') return;
    event.preventDefault();
    const route = activeRoute || { id: 'storyflow' };
    const layout = getLayout(route.id);
    const startX = event.clientX;
    const start = kind === 'explorer' ? Number(layout.explorerWidth) || 216 : Number(layout.inspectorWidth) || 296;
    const sign = kind === 'explorer' ? 1 : -1;
    const move = (moveEvent) => {
      const next = Math.max(kind === 'explorer' ? 180 : 240, Math.min(kind === 'explorer' ? 360 : 420, start + (moveEvent.clientX - startX) * sign));
      body.style.setProperty(kind === 'explorer' ? '--sf-explorer-width' : '--sf-inspector-width', `${Math.round(next)}px`);
    };
    const up = () => {
      const value = parseFloat(getComputedStyle(body).getPropertyValue(kind === 'explorer' ? '--sf-explorer-width' : '--sf-inspector-width'));
      setLayout(route.id, kind === 'explorer' ? { explorerWidth: value } : { inspectorWidth: value });
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up, { once: true });
  }

  function applyShellState() {
    const shell = qs('#studio-shell');
    if (!shell) return;
    const route = activeRoute || workspaceForPage(S.page) || { id: S.book ? 'overview' : 'dashboard', page: S.page };
    const layout = getLayout(route.id);
    shell.dataset.density = currentDensity();
    document.documentElement.dataset.shellDensity = shell.dataset.density;
    shell.dataset.workspace = route.id;
    shell.dataset.routePage = S.page || '';
    shell.dataset.panelExplorer = layout.explorer ? 'open' : 'closed';
    shell.dataset.panelInspector = layout.inspector ? 'open' : 'closed';
    shell.dataset.explorer = layout.explorer ? 'open' : 'closed';
    if (currentDensity() === 'compact' && !layout.explorer) shell.dataset.explorer = 'closed';
    qsa('[data-shell-action="focus"]').forEach((button) => button.setAttribute('aria-pressed', document.documentElement.classList.contains('studio-focus-mode') ? 'true' : 'false'));
    renderBottomPanel(layout);
    if (route.id === 'storyflow' || S.page === 'storyflow') applyStoryFlowLayout(layout);
    if (route.id !== 'storyflow') setOuterInspector(shell.dataset.outerInspector === 'open');
  }

  function togglePanel(panel) {
    const route = activeRoute || workspaceForPage(S.page) || { id: 'dashboard' };
    const layout = getLayout(route.id);
    if (panel === 'bottom') return setLayout(route.id, { bottom: !layout.bottom });
    if (panel === 'explorer') {
      setLayout(route.id, { explorer: !layout.explorer });
      if (!layout.explorer) focusCompactDrawer();
      return;
    }
    if (panel === 'inspector') {
      setLayout(route.id, { inspector: !layout.inspector });
      if (route.id !== 'storyflow') setOuterInspector(!layout.inspector);
      if (!layout.inspector) focusCompactDrawer();
    }
  }

  function toggleFocus(force) {
    const enabled = force === undefined ? !document.documentElement.classList.contains('studio-focus-mode') : Boolean(force);
    document.documentElement.classList.toggle('studio-focus-mode', enabled);
    safeStorageSet(FOCUS_KEY, enabled ? '1' : '0');
    applyShellState();
  }

  function openProjectExplorer() {
    const shell = qs('#studio-shell');
    if (!shell) return;
    const isOpen = shell.dataset.explorer === 'open';
    shell.dataset.explorer = isOpen ? 'closed' : 'open';
    if (activeRoute) setLayout(activeRoute.id, { explorer: !isOpen });
    if (!isOpen) focusCompactDrawer();
  }

  function commandItems() {
    const workspaces = workspaceMeta.map((item) => ({ id: item.id, label: item.label, hint: `Workspace / ${item.short}`, run: () => navigate(item.id) }));
    return workspaces.concat([
      { id: 'toggle-explorer', label: 'Toggle Explorer', hint: 'Panel', run: () => togglePanel('explorer') },
      { id: 'toggle-inspector', label: 'Toggle Inspector', hint: 'Panel', run: () => togglePanel('inspector') },
      { id: 'toggle-bottom', label: 'Toggle Bottom Panel', hint: 'Panel', run: () => togglePanel('bottom') },
      { id: 'focus', label: 'Toggle Focus Mode', hint: 'Ctrl+Shift+F', run: () => toggleFocus() },
      { id: 'ai', label: 'Open AI Assistant', hint: 'More', run: () => navigate('chat') },
      { id: 'tasks', label: 'Open Tasks', hint: 'More', run: () => navigate('tasks') },
    ]);
  }

  function openCommandPalette() {
    if (commandBackdrop) return;
    commandSelection = 0;
    commandBackdrop = document.createElement('div');
    commandBackdrop.className = 'studio-command-backdrop';
    commandBackdrop.innerHTML = `<div class="studio-command-palette" role="dialog" aria-modal="true" aria-label="Command Palette">
      <input class="studio-command-input" type="search" placeholder="输入命令或工作区…" aria-label="搜索命令">
      <div class="studio-command-list" role="listbox"></div>
    </div>`;
    document.body.appendChild(commandBackdrop);
    const input = qs('.studio-command-input', commandBackdrop);
    const list = qs('.studio-command-list', commandBackdrop);
    const update = () => {
      const query = String(input.value || '').trim().toLowerCase();
      const items = commandItems().filter((item) => !query || `${item.label} ${item.hint}`.toLowerCase().includes(query));
      commandSelection = Math.min(commandSelection, Math.max(0, items.length - 1));
      list.innerHTML = items.map((item, index) => `<button type="button" class="studio-command-item ${index === commandSelection ? 'is-selected' : ''}" data-command-id="${item.id}" role="option"><span>${item.label}</span><small>${item.hint}</small></button>`).join('') || '<p class="studio-explorer-empty">没有匹配的命令</p>';
      qsa('[data-command-id]', list).forEach((button) => button.addEventListener('click', () => {
        const item = commandItems().find((candidate) => candidate.id === button.dataset.commandId);
        closeCommandPalette();
        if (item) item.run();
      }));
    };
    input.addEventListener('input', update);
    input.addEventListener('keydown', (event) => {
      const items = qsa('[data-command-id]', list);
      if (event.key === 'ArrowDown') { event.preventDefault(); commandSelection = Math.min(commandSelection + 1, Math.max(0, items.length - 1)); update(); }
      if (event.key === 'ArrowUp') { event.preventDefault(); commandSelection = Math.max(0, commandSelection - 1); update(); }
      if (event.key === 'Enter' && items[commandSelection]) { event.preventDefault(); items[commandSelection].click(); }
      if (event.key === 'Tab') {
        const focusables = [input].concat(items);
        const current = focusables.indexOf(document.activeElement);
        const index = current < 0 ? 0 : current;
        const next = (index + (event.shiftKey ? -1 : 1) + focusables.length) % focusables.length;
        event.preventDefault();
        focusables[next].focus();
      }
      if (event.key === 'Escape') { event.preventDefault(); closeCommandPalette(); }
    });
    commandBackdrop.addEventListener('click', (event) => { if (event.target === commandBackdrop) closeCommandPalette(); });
    update();
    input.focus();
  }

  function closeCommandPalette() {
    if (!commandBackdrop) return;
    commandBackdrop.remove();
    commandBackdrop = null;
  }

  function closeCompactExplorer() {
    const shell = qs('#studio-shell');
    if (!shell || currentDensity() !== 'compact' || shell.dataset.explorer !== 'open') return;
    shell.dataset.explorer = 'closed';
    if (activeRoute) {
      layoutState[activeRoute.id] = Object.assign(getLayout(activeRoute.id), { explorer: false });
      writeLayoutState();
    }
  }

  function handleShellClick(event) {
    const shell = qs('#studio-shell');
    if (shell && currentDensity() === 'compact' && shell.dataset.explorer === 'open' &&
        !event.target.closest('.studio-explorer, .studio-activity-bar, [data-shell-project-switcher], #studio-global-project')) {
      closeCompactExplorer();
    }
    const route = activeRoute || workspaceForPage(S.page);
    if (currentDensity() === 'compact' &&
        ((route?.id === 'storyflow' && getLayout(route.id).inspector) || shell?.dataset.outerInspector === 'open') &&
        !event.target.closest('.storyflow-inspector, .studio-shell-inspector, [data-shell-action="outer-inspector"]')) {
      closeCompactInspector();
    }
    const routeButtonElement = event.target.closest('[data-shell-route]');
    if (routeButtonElement) {
      event.preventDefault();
      const route = routeButtonElement.dataset.shellRoute;
      if (route === 'more') return togglePanel('bottom');
      if (route === 'overview') return navigate('book');
      return navigate(route);
    }
    const moreRoute = event.target.closest('.studio-more-route');
    if (moreRoute) {
      event.preventDefault();
      return navigate(moreRoute.dataset.shellRoute || 'tasks');
    }
    const action = event.target.closest('[data-shell-action]')?.dataset.shellAction;
    if (action === 'command') return openCommandPalette();
    if (action === 'focus') return toggleFocus();
    if (action === 'focus-exit') return toggleFocus(false);
    if (action === 'panels') return togglePanel('bottom');
    if (action === 'close-bottom') return togglePanel('bottom');
    if (action === 'outer-inspector') return setOuterInspector(qs('#studio-shell')?.dataset.outerInspector !== 'open');
    if (action === 'more') {
      moreOpen = !moreOpen;
      safeStorageSet(MORE_KEY, moreOpen ? '1' : '0');
      syncNavigation();
      return;
    }
    if (event.target.closest('[data-shell-project-switcher], #studio-global-project')) return openProjectExplorer();
    const moreToggle = event.target.closest('[data-shell-more-toggle]');
    if (moreToggle) {
      moreOpen = !moreOpen;
      safeStorageSet(MORE_KEY, moreOpen ? '1' : '0');
      syncNavigation();
      return;
    }
    const bottomTab = event.target.closest('[data-shell-bottom-tab]');
    if (bottomTab && activeRoute) {
      setLayout(activeRoute.id, { bottomTab: bottomTab.dataset.shellBottomTab, bottom: true });
      return;
    }
    const bottomAction = event.target.closest('[data-shell-bottom-action]')?.dataset.shellBottomAction;
    if (bottomAction === 'open-timeline') {
      if (window.storyflow && typeof window.storyflow.open === 'function' && activeRoute?.id === 'storyflow') window.storyflow.open('timeline', '');
      else navigate('timeline');
    }
    if (bottomAction === 'open-simulation') navigate('simulation');
    if (bottomAction === 'open-tasks') navigate('tasks');
    if (bottomAction === 'open-review') navigate('review');
  }

  function handleKeydown(event) {
    const key = String(event.key || '').toLowerCase();
    if ((event.ctrlKey || event.metaKey) && key === 'k') { event.preventDefault(); openCommandPalette(); return; }
    if ((event.ctrlKey || event.metaKey) && event.shiftKey && key === 'f') { event.preventDefault(); toggleFocus(); return; }
    if (key === 'tab' && currentDensity() === 'compact') {
      const shell = qs('#studio-shell');
      const route = activeRoute || workspaceForPage(S.page);
      const drawer = shell?.dataset.explorer === 'open'
        ? qs('.studio-explorer')
        : route?.id === 'storyflow' && getLayout(route.id).inspector
          ? qs('.storyflow-inspector')
          : shell?.dataset.outerInspector === 'open'
            ? qs('.studio-shell-inspector')
            : null;
      const focusables = compactDrawerFocusables(drawer);
      if (focusables.length) {
        const current = focusables.indexOf(document.activeElement);
        const next = (current + (event.shiftKey ? -1 : 1) + focusables.length) % focusables.length;
        event.preventDefault();
        focusables[next].focus({ preventScroll: true });
        return;
      }
    }
    if (key === 'escape') {
      if (commandBackdrop) { closeCommandPalette(); return; }
      const shell = qs('#studio-shell');
      if (shell && currentDensity() === 'compact') {
        closeCompactExplorer();
        closeCompactInspector();
      }
      if (document.documentElement.classList.contains('studio-focus-mode')) toggleFocus(false);
    }
  }

  function bindEvents() {
    document.addEventListener('click', handleShellClick);
    document.addEventListener('keydown', handleKeydown);
    window.addEventListener('popstate', () => {
      const locationRoute = parseLocation();
      if (locationRoute) {
        if (S.book !== locationRoute.bookId && typeof setActiveBook === 'function') setActiveBook(locationRoute.bookId);
        navigate(locationRoute.meta, { fromLocation: true, history: false });
      } else {
        navigate('dashboard', { fromLocation: true, history: false });
      }
    });
    window.addEventListener('resize', () => {
      const shell = qs('#studio-shell');
      if (!shell) return;
      const previous = shell.dataset.density;
      shell.dataset.density = currentDensity();
      if (previous !== shell.dataset.density && activeRoute) {
        const layout = getLayout(activeRoute.id);
        if (!layoutState[activeRoute.id] || layoutState[activeRoute.id].density !== shell.dataset.density) {
          const densityPatch = { density: shell.dataset.density };
          if (shell.dataset.density === 'compact') {
            densityPatch.explorer = false;
            densityPatch.inspector = false;
            densityPatch.bottom = false;
          }
          layoutState[activeRoute.id] = Object.assign(layout, densityPatch);
          writeLayoutState();
        }
      }
      applyShellState();
    }, { passive: true });
    window.addEventListener('pagehide', () => {
      if (workspaceController) workspaceController.abort();
    }, { once: true });
  }

  function renderWithShell() {
    const serial = ++renderSerial;
    if (renderController) renderController.abort();
    const controller = new AbortController();
    renderController = controller;
    window.__studioRenderToken = serial;
    const renderPromise = (async () => {
      if (serial !== renderSerial) {
        controller.abort();
        return;
      }
      window.__studioRenderingPage = true;
      window.__studioRenderSignal = controller.signal;
      try {
        await legacyRender();
      } finally {
        if (window.__studioRenderSignal === controller.signal) {
          window.__studioRenderingPage = false;
          window.__studioRenderSignal = null;
        }
      }
      if (serial !== renderSerial || controller.signal.aborted) return;
      applyShellState();
      const route = activeRoute || workspaceForPage(S.page);
      if (route && registry.has(route.id)) {
        const entry = registry.get(route.id);
        if (entry && typeof entry.mount === 'function') entry.mount({ route, page: S.page, root: qs('#page') });
      }
      window.dispatchEvent(new CustomEvent('studio-workspace-mounted', { detail: { route, page: S.page } }));
      if (renderController === controller) renderController = null;
    })();
    // Keep failures from becoming unhandled rejections while returning the
    // actual render promise to callers that want to await a route.
    renderPromise.catch(() => {});
    return renderPromise;
  }

  function bootstrapLocation() {
    const locationRoute = parseLocation();
    if (locationRoute) {
      if (S.book !== locationRoute.bookId && typeof setActiveBook === 'function') setActiveBook(locationRoute.bookId);
      return navigate(locationRoute.meta, { fromLocation: true, history: false });
    }
    const stateRoute = workspaceForPage(S.page) || { id: S.book ? 'overview' : 'dashboard', page: S.page || 'dashboard' };
    if (S.book && stateRoute.id !== 'dashboard') {
      activeRoute = stateRoute;
      activePage = internalPage(stateRoute);
      changeUrl(stateRoute, true);
    } else {
      activeRoute = stateRoute;
      activePage = stateRoute.page;
    }
    beginWorkspaceRequestScope();
    renderNavigation();
    applyShellState();
    renderWithShell();
  }

  function initialize() {
    if (initialized) return;
    registerBuiltIns();
    moreOpen = safeStorageGet(MORE_KEY, '0') === '1';
    window.renderNav = renderNavigation;
    window.render = renderWithShell;
    window.go = navigate;
    window.startStudioShell = initialize;
    window.StudioShell = {
      registry,
      state: {
        get global() { return { density: currentDensity(), focus: document.documentElement.classList.contains('studio-focus-mode') }; },
        get project() { return { id: S.book || null, books: S.books || [] }; },
        get workspace() { return activeRoute ? Object.assign({}, activeRoute) : null; },
        get panel() { return activeRoute ? Object.assign({}, getLayout(activeRoute.id)) : null; },
        selection: {},
      },
      registerWorkspace,
      navigate,
      togglePanel,
      toggleFocus,
      openCommandPalette,
      setOuterInspector,
      getLayout,
      lifecycle: {
        get signal() { return renderController ? renderController.signal : null; },
        get workspaceSignal() { return workspaceController ? workspaceController.signal : null; },
        get rendering() { return Boolean(window.__studioRenderingPage); },
      },
    };
    bindEvents();
    const focus = safeStorageGet(FOCUS_KEY, '0') === '1';
    if (focus) document.documentElement.classList.add('studio-focus-mode');
    initialized = true;
    bootstrapLocation();
  }

  // The loader calls startStudioShell after all legacy page adapters are
  // registered. Keeping initialization explicit removes the first-paint race
  // without changing the compatibility entry point for the rest of the app.
  window.startStudioShell = initialize;
}());
