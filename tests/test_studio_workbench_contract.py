"""Contract checks for the first-class Studio workbench shell."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "src/web/static/index.html").read_text(encoding="utf-8")
SHELL_JS = (ROOT / "src/web/static/studio-shell.js").read_text(encoding="utf-8")
SHELL_CSS = (ROOT / "src/web/static/studio-shell.css").read_text(encoding="utf-8")
STUDIO = (ROOT / "src/web/studio.py").read_text(encoding="utf-8")
ENHANCEMENTS = (ROOT / "src/web/static/studio-enhancements.js").read_text(encoding="utf-8")


def test_application_shell_has_one_global_bar_activity_explorer_main_and_bottom_panel():
    for marker in (
        'id="studio-global-bar"',
        'id="studio-activity-bar"',
        'id="studio-sidebar"',
        'class="main studio-main"',
        'id="studio-bottom-panel"',
    ):
        assert marker in INDEX


def test_first_class_workspaces_have_a_real_route_registry_and_history_urls():
    for workspace in ("write", "plan", "storyflow", "canon", "review", "timeline"):
        assert f"id: '{workspace}'" in SHELL_JS
        assert "/${encodeURIComponent(meta.id)}" in SHELL_JS or "routePath(meta)" in SHELL_JS
    assert "window.history[method]" in SHELL_JS
    assert "parseLocation()" in SHELL_JS
    assert "/project/{book_id}/{workspace}" in STUDIO
    assert "/project/{book_id}/more/{more_page}" in STUDIO


def test_shell_owns_scoped_layout_state_and_density_breakpoints():
    assert "novelforge-workbench-layout-v1" in SHELL_JS
    assert "workspace: workspaceId" in SHELL_JS
    assert "width < DENSITIES.compact" in SHELL_JS
    assert "width < DENSITIES.standard" in SHELL_JS
    assert "data-shell-density" in SHELL_CSS
    assert "data-density=\"compact\"" in SHELL_CSS
    assert "data-density=\"expanded\"" in SHELL_CSS


def test_storyflow_has_compact_overlay_and_unified_bottom_panel_contract():
    assert "data-panel-explorer" in SHELL_CSS
    assert "data-panel-inspector" in SHELL_CSS
    assert "data-shell-resize" in SHELL_JS
    assert "bottomHeight" in SHELL_JS
    assert "studio-bottom-resize" in SHELL_CSS
    for tab in ("Timeline", "Simulation", "Event Log", "Runs", "Problems"):
        assert tab in SHELL_JS
    assert ".studio-bottom-panel" in SHELL_CSS


def test_shell_exposes_command_palette_focus_mode_and_workspace_lifecycle():
    assert "Command Palette" in SHELL_JS
    assert "Ctrl+Shift+F" in SHELL_JS
    assert "registerWorkspace" in SHELL_JS
    assert "deactivateWorkspace" in SHELL_JS
    assert "studio-workspace-mounted" in SHELL_JS
    assert "destroy," in (ROOT / "src/web/static/studio-storyflow.js").read_text(encoding="utf-8")
    assert "closeCompactExplorer" in SHELL_JS
    assert "closeCompactInspector" in SHELL_JS
    assert "workspaceSignal" in SHELL_JS
    assert "typeof window.closeModal === 'function'" in SHELL_JS
    assert "event.key === 'Tab'" in SHELL_JS
    assert "!event.target.closest('.studio-explorer, .studio-activity-bar" in SHELL_JS
    assert 'data-shell-action="focus-exit"' in INDEX


def test_task_polling_has_an_explicit_timeout_error_and_last_snapshot():
    assert "lastTask=null" in INDEX
    assert "TASK_POLL_TIMEOUT" in INDEX
    assert "timeout.lastTask=lastTask" in INDEX


def test_continuous_page_watcher_cannot_survive_navigation_or_stop_listening():
    assert "async function pollTask(id, signal=window.__studioWorkspaceSignal)" in INDEX
    assert "_contTask!==id || signal?.aborted" in INDEX
    assert "api('GET','/tasks/'+id,undefined,undefined,{signal})" in INDEX
    assert "pageTimeout(()=>pollTask(id,signal)" in INDEX


def test_legacy_page_async_work_obeys_workspace_abort_and_cleanup_boundary():
    assert "function pageTimeout(callback, delay, signal)" in INDEX
    assert "function pageSleep(delay, signal)" in INDEX
    assert "async function waitForTask(taskId, renderStatus, maxPolls=300, signal=pageSignal())" in INDEX
    assert "await pageSleep(1000,signal)" in INDEX
    assert "pageTimeout(()=>monitorWriteTask(tid,signal),2000,signal)" in INDEX
    assert "pageTimeout(async()=>" in INDEX
    assert "await api('POST','/books/'+encodeURIComponent(r.id)+'/planning-sources',form,true)" in INDEX
    assert "if(!signal?.aborted&&S.page==='import')await PAGES.import" in INDEX
    assert "if(window._chapterEditorCleanup)window._chapterEditorCleanup();" in INDEX
    assert "clearInterval(window._chapterEditorAutoInterval)" in INDEX
    assert "pageTimeout(()=>window.storyflow?.open(view, focus), 450)" in INDEX


def test_dashboard_health_failure_is_not_rendered_as_normal():
    assert "let health=null,tasks=[],healthError=false;" in INDEX
    assert "catch(_){healthError=true;}" in INDEX
    assert "const healthOk=!!health&&health.status!=='unhealthy'" in INDEX
    assert "健康状态读取失败，当前系统状态不能确认。" in INDEX


def test_ai_runtime_page_exposes_persisted_runtime_center_and_compute_policy():
    for marker in (
        "runtime-center",
        "/runtime/registry",
        "/runtime/capabilities",
        "/compute/policy",
        "runtimePlaneAction",
        "Tool Gateway catalog",
    ):
        assert marker in ENHANCEMENTS


def test_shell_keeps_more_functionality_out_of_the_primary_workspace_tree():
    assert "More 辅助功能" in SHELL_JS
    for label in ("AI Assistant", "Tasks", "Import / Export", "Settings"):
        assert label in SHELL_JS
    assert "studio-explorer-more-list" in SHELL_CSS


def test_simulation_round_controls_fail_closed_until_run_is_running():
    simulation = (ROOT / "src/web/static/studio-simulation.js").read_text(encoding="utf-8")
    assert 'data-sim-round-gate' in simulation
    assert "Round execution is available only while this run is RUNNING" in simulation
    assert "execute.disabled = provider || !roundReady" in simulation
    assert "queue.disabled = !roundReady" in simulation
    assert "Round execution requires RUNNING" in simulation
