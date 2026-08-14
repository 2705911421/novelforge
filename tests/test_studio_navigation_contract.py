"""Static contract checks for the current Studio navigation and unified setup page."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "src/web/static/index.html").read_text(encoding="utf-8")
ENHANCEMENTS = (ROOT / "src/web/static/studio-enhancements.js").read_text(encoding="utf-8")


def test_navigation_has_one_ai_configuration_entry_and_no_visible_legacy_services_entry():
    nav_source = INDEX.split("// ===== API =====", 1)[0]
    assert "id:'services'" not in nav_source
    assert nav_source.count("id:'agent-config'") == 1
    assert "label:'全局创作参数'" in nav_source
    assert "label:'模型配置'" not in nav_source


def test_book_navigation_is_grouped_and_unavailable_pages_are_not_fake_buttons():
    assert "navGroup" in nav_source_after_nav_definition()
    assert "if(!navPageAvailable(item)) return '';" in INDEX
    assert "nav-extension-group" in INDEX
    assert "addNav({ id: 'extension-home'" not in ENHANCEMENTS
    assert "addNav({ id: 'agent-config'" not in ENHANCEMENTS


def test_studio_opens_on_paper_and_preserves_explicit_dark_preference():
    """The authoring default is warm paper; dark mode remains an opt-in choice."""

    assert "localStorage.getItem('novelforge-theme')==='dark'?'dark':'light'" in INDEX
    assert "document.documentElement.dataset.theme='light'" in INDEX
    assert "setTheme(root.dataset.theme || 'light')" in (
        ROOT / "src/web/static/studio-visual.js"
    ).read_text(encoding="utf-8")
    assert "--sf-canvas-bg: #eeeae2" in (
        ROOT / "src/web/static/storyflow.css"
    ).read_text(encoding="utf-8")


def test_storyflow_surfaces_model_readiness_before_model_backed_actions():
    """Planning remains available while AI actions truthfully require setup."""

    assert "loadModelReadiness();" in STORYFLOW_JS
    assert "/creation/preflight?mode=planned&bookId=" in STORYFLOW_JS
    assert "AI RUNTIME · SETUP REQUIRED" in STORYFLOW_JS
    assert "data-sf-action=\"open-model-config\"" in STORYFLOW_JS
    assert "modelRuntimeReady()" in STORYFLOW_JS
    assert ".sf-runtime-badge" in (
        ROOT / "src/web/static/storyflow.css"
    ).read_text(encoding="utf-8")


def test_services_is_only_a_deep_link_compatibility_alias():
    assert "if(typeof PAGES['agent-config']==='function') return PAGES['agent-config'](p);" in INDEX
    assert "PAGES.services = PAGES['agent-config'];" in ENHANCEMENTS


def test_legacy_story_visualization_entries_route_to_storyflow_views():
    assert "const STORYFLOW_COMPAT_ROUTES = Object.freeze({" in INDEX
    for page, view in {
        "mindmap": "story",
        "timeline": "timeline",
        "plot": "story",
        "world-map": "world",
        "foreshadowing": "foreshadow",
        "characters": "character",
    }.items():
        assert f"{page}: '{view}'" in INDEX or f"'{page}': '{view}'" in INDEX
    assert "if(routeStoryFlowCompatibility(page)) return;" in INDEX
    assert "const existingIntent = window.__storyflowRouteIntent || {};" in STORYFLOW_JS
    assert "existingIntent.view || legacyRouteViews[S.page] || 'story'" in STORYFLOW_JS


def test_legacy_storyflow_module_keeps_old_api_pages_as_compatibility_fallbacks():
    assert "PAGES.mindmap=async(p)=>" in INDEX
    assert "PAGES.timeline=async(p)=>" in INDEX
    assert "PAGES.plot=async(p)=>" in INDEX
    assert "PAGES['world-map']=async(p)=>" in INDEX
    assert "PAGES.foreshadowing=async(p)=>" in INDEX
    assert "PAGES.characters=async(p)=>" in INDEX


def test_storyflow_node_actions_keep_focus_inside_the_shared_controller():
    """Opening a graph entity must not discard the selected StoryFlow node."""

    assert "if (node.type === 'Character') { openStoryFlowView('character', node.id); return; }" in STORYFLOW_JS
    assert "if (node.type === 'Foreshadow') { openStoryFlowView('foreshadow', node.id); return; }" in STORYFLOW_JS
    assert "if (node.type === 'Location' || node.type === 'Faction') { openStoryFlowView('world', node.id); return; }" in STORYFLOW_JS
    assert "const hiddenNodes = realNodes()" in STORYFLOW_JS
    assert "data-sf-restore-hidden" in STORYFLOW_JS
    assert "function restoreHiddenNode(node)" in STORYFLOW_JS


def test_storyflow_view_switch_preserves_a_compatible_focus_node():
    """View changes should keep the author's current graph anchor when legal."""

    assert "const targetTypes = new Set(VIEW_TYPES[nextView] || []);" in STORYFLOW_JS
    assert "const currentFocus = state.focus ? nodeById(state.focus) : null;" in STORYFLOW_JS
    assert "targetTypes.has(currentFocus.type)" in STORYFLOW_JS
    assert "state.view === 'context'" in STORYFLOW_JS
    assert "(preservedFocus?.id || '')" in STORYFLOW_JS


def test_storyflow_minimap_can_drag_the_viewport_without_request_fanout():
    """The minimap viewport is a navigable workspace control, not decoration."""

    assert "data-sf-minimap-viewport=\"1\"" in STORYFLOW_JS
    assert "state.minimapDrag = {" in STORYFLOW_JS
    assert "minimap.setPointerCapture(event.pointerId)" in STORYFLOW_JS
    assert "if (!state.pan && !state.minimapDrag) scheduleViewportFetch();" in STORYFLOW_JS
    assert "const finishMinimapDrag = (event) =>" in STORYFLOW_JS


def test_storyflow_edges_use_semantic_port_anchors_with_legacy_fallback():
    """The visible edge endpoint must agree with the persisted Story Port."""

    assert "const sourcePort = edge.sourcePort || edge.source_port || '';" in STORYFLOW_JS
    assert "const targetPort = edge.targetPort || edge.target_port || '';" in STORYFLOW_JS
    assert "const sourceAnchor = edgeAnchor(source, 'output', sourcePort);" in STORYFLOW_JS
    assert "const targetAnchor = edgeAnchor(target, 'input', targetPort);" in STORYFLOW_JS
    assert "function edgeAnchor(node, direction, portName)" in STORYFLOW_JS
    assert "return elementGraphCenter(port);" in STORYFLOW_JS


def test_dense_storyflow_edges_use_canvas_paint_with_semantic_hit_testing():
    """Dense edge paint must not regress to one SVG DOM element per edge."""

    assert "const DENSE_EDGE_THRESHOLD = 40;" in STORYFLOW_JS
    assert "id=\"sf-edge-canvas\"" in STORYFLOW_JS
    assert "canvas-2d" in STORYFLOW_JS
    assert "function drawDenseEdges(records = renderedEdgeRecords())" in STORYFLOW_JS
    assert "function denseEdgeHit(clientX, clientY)" in STORYFLOW_JS
    assert "const edge = denseEdgeHit(event.clientX, event.clientY);" in STORYFLOW_JS
    assert "canvas.dataset.renderedEdges = String(records.length);" in STORYFLOW_JS
    assert "function clearEdgeCanvas()" in STORYFLOW_JS
    assert "canvas.dataset.edgePaintedEdges = '0';" in STORYFLOW_JS


def test_context_inspector_surfaces_manifest_source_availability():
    assert "const sourceAvailability = tokenSummary.sourceAvailability" in STORYFLOW_JS
    assert "Source availability" in STORYFLOW_JS


def test_full_graph_viewport_pages_merge_without_overwriting_local_workspace_state():
    """Viewport reads extend the bounded projection instead of replacing the Canvas graph."""

    assert "function mergeViewportGraph(base, page)" in STORYFLOW_JS
    assert "state.graph = mergeViewportGraph(state.graph, graph);" in STORYFLOW_JS
    assert "const preserveLocalLayout = Boolean(state.layoutDirty);" in STORYFLOW_JS
    assert "canvas.dataset.loadedGraphNodes = String(realNodes().length);" in STORYFLOW_JS
    assert "state.viewportPages?.add(requestKey);" in STORYFLOW_JS
    assert "if (!state.pan) scheduleViewportFetch();" in STORYFLOW_JS
    assert "canvas.classList.remove('is-panning');\n      scheduleViewportFetch();" in STORYFLOW_JS


def test_full_graph_starts_with_a_viewport_sized_working_set():
    """The explicit Full Graph must not ship a large compatibility payload first."""

    assert "const boundedLimit = '240';" in STORYFLOW_JS
    assert "if (state.view === 'all') params.set('edge_limit', '600');" in STORYFLOW_JS
    assert "1200-node/3000-edge compatibility" in STORYFLOW_JS
    assert "Number(state.graph.meta.totalAvailableNodes || 0) > Number(state.graph.nodes?.length || 0)" in STORYFLOW_JS


def test_full_graph_viewport_exposes_cursor_continuation_and_explicit_page_action():
    assert "page_token" in STORYFLOW_JS
    assert "function currentViewportContinuation" in STORYFLOW_JS
    assert "state.viewportContinuation = page.hasMore && page.nextPageToken" in STORYFLOW_JS
    assert "function loadNextViewportPage" in STORYFLOW_JS
    assert "data-sf-action=\"load-next-viewport-page\"" in STORYFLOW_JS


def test_full_graph_viewport_exposes_independent_semantic_edge_continuation():
    assert "edge_page_token" in STORYFLOW_JS
    assert "nextInternalEdgePageToken" in STORYFLOW_JS
    assert "function currentViewportEdgeContinuation" in STORYFLOW_JS
    assert "function loadNextViewportEdgePage" in STORYFLOW_JS
    assert "data-sf-action=\"load-next-viewport-edge-page\"" in STORYFLOW_JS
    assert "loadedInternalEdgeCount" in STORYFLOW_JS


def test_full_graph_viewport_surfaces_cross_boundary_semantic_edges():
    assert "crossBoundaryEdgeCount" in STORYFLOW_JS
    assert "crossBoundaryEdges" in STORYFLOW_JS
    assert "function viewportBoundaryEdgesForNode(nodeId)" in STORYFLOW_JS
    assert "data-sf-boundary-node" in STORYFLOW_JS
    assert "These are recorded SQLite relationships." in STORYFLOW_JS
    assert "Boundary means outside the current world-coordinate page" in STORYFLOW_JS


def test_full_graph_boundary_edges_have_an_authoritative_paged_read_action():
    assert "boundary_page_token" in STORYFLOW_JS
    assert "boundary_node_id" in STORYFLOW_JS
    assert "function loadNextBoundaryPage(nodeId)" in STORYFLOW_JS
    assert "nextBoundaryPageToken" in STORYFLOW_JS
    assert "Load more boundary edges" in STORYFLOW_JS


def test_full_graph_search_keeps_bounded_boundary_metadata_for_inspector_actions():
    assert "keepFullGraphViewport" in STORYFLOW_JS
    assert "loadFocusedSearchResultInViewport(id, canvasViewportBounds())" in STORYFLOW_JS
    assert "loadNodeDetail(nodeId);" in STORYFLOW_JS


def test_full_graph_viewport_merge_does_not_erase_an_explicit_boundary_cursor():
    assert "preserveBoundaryPage" in STORYFLOW_JS
    assert "pageHasExplicitBoundaryPage" in STORYFLOW_JS
    assert "state.selected?.has(state.boundaryNodeId)" in STORYFLOW_JS
    assert "nextBoundaryPageToken: preserveBoundaryPage" in STORYFLOW_JS
    assert "state.boundaryNodeId = nextMeta.nextBoundaryPageToken ? nodeId : '';" in STORYFLOW_JS


def test_boundary_cursor_reuses_the_authoritative_viewport_bounds_after_focus_centering():
    assert "function viewportBoundsFromMetadata(viewport)" in STORYFLOW_JS
    assert "const viewport = state.boundaryViewport || canvasViewportBounds();" in STORYFLOW_JS
    assert "state.boundaryViewport = viewportBoundsFromMetadata(graph.meta?.viewport) || viewport;" in STORYFLOW_JS
    assert "if (target) centerOn(target);" not in STORYFLOW_JS


def test_full_graph_search_invalidates_inflight_normal_viewport_continuations():
    assert "viewportGeneration" in STORYFLOW_JS
    assert "requestGeneration !== Number(state.viewportGeneration || 0)" in STORYFLOW_JS
    assert "state.viewportContinuation = null;" in STORYFLOW_JS
    assert "state.viewportPages = new Set();" in STORYFLOW_JS


def test_storyflow_selection_inspector_uses_authoritative_semantic_projection():
    assert "/story-graph/selection?" in STORYFLOW_JS
    assert "function loadSelectionProjection(nodes)" in STORYFLOW_JS
    assert "function renderSelectionProjection(projection, nodes)" in STORYFLOW_JS
    assert "sqlite.story_graph_projection" in STORYFLOW_JS
    assert "inside edges" in STORYFLOW_JS
    assert "outside selection" in STORYFLOW_JS
    assert "这组节点是一个可执行的 StoryFlow 工作单元" in STORYFLOW_JS
    assert "data-sf-selection-focus-type" in STORYFLOW_JS
    assert "issue a fresh authoritative focus query" in STORYFLOW_JS


def test_storyflow_exposes_safe_canon_before_overlay_recovery_action():
    assert "/story-graph/planning/reconciliation-candidates" in STORYFLOW_JS
    assert "/story-graph/planning/reconcile" in STORYFLOW_JS
    assert "ACCEPTED_PENDING_OVERLAY" in STORYFLOW_JS
    assert "reconcilePlanningTask" in STORYFLOW_JS


STORYFLOW_JS = (ROOT / "src/web/static/studio-storyflow.js").read_text(encoding="utf-8")


def nav_source_after_nav_definition() -> str:
    return INDEX.split("// ===== API =====", 1)[0]
