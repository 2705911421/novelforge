/* global PAGES, S, api, header, esc, toast, go */
(function () {
  'use strict';

  let state = null;

  function bookPath() { return `/books/${encodeURIComponent(S.book)}/simulation`; }
  function text(value) { return esc(String(value == null ? '' : value)); }
  function jsonText(value) {
    try { return text(JSON.stringify(value == null ? {} : value, null, 2)); }
    catch (_) { return text(String(value == null ? '' : value)); }
  }
  function compactValue(value) {
    if (Array.isArray(value)) return value.some((item) => item && typeof item === 'object') ? JSON.stringify(value) : value.join(', ');
    if (value && typeof value === 'object') return JSON.stringify(value);
    return String(value == null ? '' : value);
  }
  function statusClass(status) { return `simulation-status simulation-status-${String(status || '').toLowerCase()}`; }
  function runSummary(run) {
    return `${run.currentRound}/${run.maxRounds} rounds · ${run.status}`;
  }

  // Keep the author-facing selector aligned with the backend ActionType
  // ontology.  The server remains authoritative and validates every action;
  // this list only prevents the UI from hiding valid typed actions.
  const ACTION_TYPES = [
    'MOVE', 'OBSERVE', 'TALK', 'ASK', 'ANSWER', 'INFORM', 'DECEIVE',
    'HIDE_INFORMATION', 'DISCLOSE_SECRET', 'INVESTIGATE', 'PLAN', 'WAIT',
    'ATTACK', 'DEFEND', 'HELP', 'BETRAY', 'FORM_ALLIANCE', 'BREAK_ALLIANCE',
    'CHANGE_RELATIONSHIP', 'USE_ITEM', 'ACQUIRE_ITEM', 'LOSE_ITEM',
    'PURSUE_GOAL', 'ABANDON_GOAL', 'REACT_TO_EVENT', 'MAKE_DECISION',
    'SEND_MESSAGE', 'SUMMON', 'FLEE',
  ];

  function renderActionOptions(selected = 'WAIT') {
    return ACTION_TYPES.map((action) => `<option value="${action}" ${action === selected ? 'selected' : ''}>${action}</option>`).join('');
  }

  function renderProviderAgentOptions() {
    const agents = Array.isArray(state?.agents) ? state.agents : [];
    const activeIds = new Set((state?.scheduler?.activeAgents || []).map((id) => String(id)));
    const ordered = [...agents].sort((left, right) => {
      const activeDelta = Number(activeIds.has(String(right.id))) - Number(activeIds.has(String(left.id)));
      return activeDelta || String(left.id).localeCompare(String(right.id));
    });
    const visible = ordered.slice(0, 100);
    const options = visible.map((agent) => {
      const id = String(agent.id);
      const active = activeIds.has(id);
      return `<option value="${text(id)}" ${active ? 'selected' : ''}>${text(agent.name)} · ${text(agent.type)}${active ? ' · ACTIVE' : ''}</option>`;
    }).join('');
    const overflow = ordered.length > visible.length
      ? ` <small class="simulation-evidence-note">Showing ${visible.length} of ${ordered.length}; scheduler still evaluates the full persisted roster.</small>`
      : '';
    return `<label>Provider agents<select class="input" name="providerAgentIds" multiple size="4" aria-describedby="simulation-provider-agents-note">${options}</select><small id="simulation-provider-agents-note">Selected Agents are author-pinned for this provider round; clear the selection to let the persisted scheduler choose active slots.${overflow}</small></label>`;
  }

  const WORKSPACES = [
    { id: 'world', label: 'WORLD', hint: 'Snapshot & environment' },
    { id: 'agents', label: 'AGENTS', hint: 'Roster & activation' },
    { id: 'simulate', label: 'SIMULATE', hint: 'Run & intervene' },
    { id: 'analyze', label: 'ANALYZE', hint: 'Evidence & compare' },
    { id: 'interact', label: 'INTERACT', hint: 'Characters & survey' },
    { id: 'history', label: 'HISTORY', hint: 'Runs & adoption' },
  ];

  function activeWorkspace() {
    return WORKSPACES.some((item) => item.id === state.workspace) ? state.workspace : 'simulate';
  }

  function workspaceStorageKey() {
    return `novelforge-simulation-workspace-${S.book || 'global'}`;
  }

  function initialWorkspace() {
    try {
      const saved = window.localStorage.getItem(workspaceStorageKey());
      if (WORKSPACES.some((item) => item.id === saved)) return saved;
    } catch (_) { /* workspace selection is optional presentation state */ }
    return 'simulate';
  }

  function renderWorkspaceNav() {
    const selected = activeWorkspace();
    return `<div class="simulation-workflow-shell">
      <nav class="simulation-workspace-nav" aria-label="Simulation workspaces">
        ${WORKSPACES.map((item) => `<button class="simulation-workspace-tab ${item.id === selected ? 'is-active' : ''}" type="button" data-sim-workspace="${item.id}" aria-pressed="${item.id === selected}"><b>${item.label}</b><small>${item.hint}</small></button>`).join('')}
      </nav>
      <div class="simulation-workflow-status"><span class="simulation-workflow-kicker">AUTHOR CONTROLLED SANDBOX</span><span>State is loaded from durable backend records · Canon read only</span></div>
    </div>`;
  }

  async function loadRuns() {
    const response = await api('GET', `${bookPath()}/runs`);
    state.runs = response.runs || [];
    if (!state.runId && state.runs.length) state.runId = state.runs[0].id;
    if (state.runId && !state.runs.some((run) => run.id === state.runId)) state.runId = state.runs[0]?.id || '';
  }

  async function loadBranchTree() {
    const response = await api('GET', `${bookPath()}/branches`);
    state.branchTree = response || { nodes: [], edges: [] };
  }

  async function loadActiveRun() {
    closeEventStream();
    if (!state.runId) {
      state.detail = null;
      state.events = [];
      state.eventDetails = {};
      return;
    }
    const runPath = `${bookPath()}/runs/${encodeURIComponent(state.runId)}`;
    let detail;
    let events;
    try {
      [detail, events] = await Promise.all([
        api('GET', runPath),
        api('GET', `${runPath}/events?after_sequence=0&limit=200`),
      ]);
    } catch (error) { throw new Error(`Run detail or event ledger unavailable: ${error.message}`); }
    state.detail = detail;
    state.events = events.events || [];
    state.eventDetails = {};
    state.comparison = null;
    state.scheduler = null;
    state.budget = null;
    state.causality = null;
    let roster;
    try { roster = await api('GET', `${runPath}/agents`); }
    catch (error) { throw new Error(`Agent roster unavailable: ${error.message}`); }
    state.agents = roster.agents || [];
    state.analysisError = '';
    const roundNumber = (detail.run.currentRound || 0) + 1;
    const [schedulerResult, adoptionResult, graphResult, reportResult, outcomesResult, interventionResult] = await Promise.allSettled([
      api('GET', `${runPath}/scheduler?roundNumber=${encodeURIComponent(roundNumber)}`),
      api('GET', `${runPath}/adoptions`),
      api('GET', `${runPath}/graph?event_limit=1000`),
      api('GET', `${runPath}/analysis?limit=20`),
      api('GET', `${runPath}/outcomes`),
      api('GET', `${runPath}/interventions?limit=100`),
    ]);
    if (schedulerResult.status === 'fulfilled') state.scheduler = schedulerResult.value;
    else state.analysisError = `Scheduler unavailable: ${schedulerResult.reason.message}`;
    try {
      state.budget = await api('GET', `${runPath}/budget?estimatedCalls=${encodeURIComponent((state.scheduler?.activeAgents || []).length)}`);
    } catch (error) {
      state.analysisError = state.analysisError || `Budget unavailable: ${error.message}`;
    }
    if (adoptionResult.status === 'fulfilled') state.proposals = adoptionResult.value.proposals || [];
    else throw new Error(`Adoption proposals unavailable: ${adoptionResult.reason.message}`);
    if (interventionResult.status === 'fulfilled') state.interventions = interventionResult.value.interventions || [];
    else { state.interventions = []; state.analysisError = state.analysisError || `Interventions unavailable: ${interventionResult.reason.message}`; }
    state.graph = null;
    state.reports = [];
    state.outcomes = null;
    if (graphResult.status === 'fulfilled') state.graph = graphResult.value;
    else state.analysisError = state.analysisError || `Graph unavailable: ${graphResult.reason.message}`;
    if (reportResult.status === 'fulfilled') state.reports = reportResult.value.reports || [];
    else state.analysisError = state.analysisError || `Reports unavailable: ${reportResult.reason.message}`;
    if (outcomesResult.status === 'fulfilled') state.outcomes = outcomesResult.value;
    else state.analysisError = state.analysisError || `Outcome clusters unavailable: ${outcomesResult.reason.message}`;
    const characters = state.agents.filter((agent) => agent.type === 'character');
    state.chatAgentId = characters.some((agent) => agent.id === state.chatAgentId)
      ? state.chatAgentId : (characters[0]?.id || '');
    state.interactionError = '';
    const chatRequest = state.chatAgentId
      ? api('GET', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/agents/${encodeURIComponent(state.chatAgentId)}/chat?limit=50`)
      : Promise.resolve({ interactions: [] });
    const [chatResult, surveyResult] = await Promise.allSettled([
      chatRequest,
      api('GET', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/survey?limit=20`),
    ]);
    if (chatResult.status === 'fulfilled') state.chatInteractions = chatResult.value.interactions || [];
    else { state.chatInteractions = []; state.interactionError = chatResult.reason.message; }
    if (surveyResult.status === 'fulfilled') state.surveys = surveyResult.value.surveys || [];
    else { state.surveys = []; state.interactionError = state.interactionError || surveyResult.reason.message; }
    connectEventStream();
  }

  function closeEventStream() {
    if (state?.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
  }

  function connectEventStream() {
    if (!state?.runId || typeof window.EventSource !== 'function') return;
    const afterSequence = state.events.reduce((highest, event) => Math.max(highest, Number(event.sequence) || 0), 0);
    const source = new window.EventSource(
      `/api/v1${bookPath()}/runs/${encodeURIComponent(state.runId)}/events/stream?after_sequence=${afterSequence}`,
    );
    state.eventSource = source;
    source.addEventListener('simulation_event', (message) => {
      let event;
      try { event = JSON.parse(message.data); } catch (_) { return; }
      const sequence = Number(event.sequence);
      if (event.runId !== state.runId || !Number.isInteger(sequence) || sequence < 1) return;
      if (state.events.some((item) => Number(item.sequence) === sequence)) return;
      state.events = [...state.events, event].sort((left, right) => Number(left.sequence) - Number(right.sequence));
      const body = document.querySelector('[data-sim-timeline-body]');
      if (body) body.innerHTML = renderTimeline();
      const metric = document.querySelector('[data-sim-event-sequence]');
      if (metric) metric.textContent = String(sequence);
      // The backend projects the Sandbox graph during the same durable round;
      // refresh its read model on the live event stream so graph state does
      // not lag behind the timeline.  Failures stay silent here and remain
      // visible through the explicit Graph refresh control.
      void refreshGraph(true);
    });
    source.onerror = () => {
      if (source.readyState === window.EventSource.CLOSED && state.eventSource === source) state.eventSource = null;
    };
  }

  async function refresh() {
    state.loading = true;
    state.error = '';
    render();
    try {
      await loadRuns();
      await loadBranchTree();
      await loadActiveRun();
    } catch (error) {
      state.error = error.message;
    } finally {
      state.loading = false;
      render();
    }
  }

  function renderRuns() {
    if (!state.runs.length) {
      return '<div class="simulation-empty"><strong>No sandbox runs yet</strong><span>Create an immutable world snapshot to begin a what-if run.</span></div>';
    }
    return state.runs.map((run) => `<button class="simulation-run-row ${run.id === state.runId ? 'is-active' : ''}" data-sim-run="${text(run.id)}">
      <span class="simulation-run-title">${text(run.name)}</span>
      <span class="${statusClass(run.status)}">${text(run.status)}</span>
      <small>${text(runSummary(run))}</small>
    </button>`).join('');
  }

  function renderBranchTree() {
    const nodes = Array.isArray(state.branchTree?.nodes) ? state.branchTree.nodes : [];
    if (!nodes.length) return '<p class="dim-note">No persisted branch tree yet. Fork this run to create an isolated child.</p>';
    const byParent = new Map();
    nodes.forEach((node) => {
      const parent = node.parentRunId || '';
      if (!byParent.has(parent)) byParent.set(parent, []);
      byParent.get(parent).push(node);
    });
    const seen = new Set();
    function renderNode(node, depth) {
      if (seen.has(node.runId)) return '';
      seen.add(node.runId);
      const children = byParent.get(node.runId) || [];
      return `<div class="simulation-branch-node" style="--simulation-branch-depth:${depth}">
        <button class="simulation-branch-node-main ${node.runId === state.runId ? 'is-active' : ''}" data-sim-run="${text(node.runId)}">
          <span><b>${text(node.name)}</b><small>${text(node.runId)}</small></span><span class="${statusClass(node.status)}">${text(node.status)}</span>
        </button>
        <small class="simulation-branch-meta">${node.isRoot ? 'Root run' : `Forked at event ${text(node.forkSequence)}`} 路 ${text(runSummary(node))}</small>
        ${children.map((child) => renderNode(child, depth + 1)).join('')}
      </div>`;
    }
    const roots = byParent.get('') || nodes.filter((node) => !node.parentRunId);
    const tree = roots.map((root) => renderNode(root, 0)).join('');
    const unattached = nodes.filter((node) => !seen.has(node.runId)).map((node) => renderNode(node, 0)).join('');
    return `<div class="simulation-branch-tree">${tree}${unattached}</div><small class="simulation-evidence-note">Persisted simulation branch edges 路 canonicalMutation=false</small>`;
  }

  function renderTimeline() {
    if (!state.events.length) return '<div class="simulation-empty simulation-empty-compact">No persisted sandbox events.</div>';
    return state.events.map((event) => {
      const targets = Array.isArray(event.targetIds) ? event.targetIds.join(', ') : '';
      const detail = state.eventDetails?.[event.id];
      return `<details class="simulation-event"><summary data-sim-event-toggle="${text(event.id)}"><div><b>#${text(event.sequence)}</b><span>${text(event.type)}</span></div>
        <p>${text(event.actorId || 'Author')} ${targets ? `→ ${text(targets)}` : ''}</p>
        <small>Round ${text(event.round)} · ${text(event.simulationTime || 'simulation time unknown')} · ${text(event.location || 'location unknown')} · ${text(event.visibilityScope || 'world')} · click for persisted evidence</small></summary>
        <div class="simulation-event-detail" data-sim-event-detail="${text(event.id)}">${detail ? renderEventInspector(detail) : '<p class="dim-note">Open this event to load Actor, Memory, Context, Why, State Delta, and related graph changes from durable Sandbox evidence.</p>'}</div>
      </details>`;
    }).join('');
  }

  function renderEventInspector(detail) {
    const event = detail?.event || {};
    const actor = detail?.actor;
    const memory = detail?.memory || {};
    const context = detail?.context;
    const why = detail?.why || {};
    const delta = detail?.stateDelta || {};
    const graph = detail?.relatedGraphChanges || {};
    const causes = (why.causedBy || []).map((cause) => `<li><b>${text(cause.causeType)}</b> ${text(cause.causeId)} · ${text(cause.relation)}</li>`).join('');
    const memoryRows = (memory.items || []).map((item) => `<li><b>${text(item.type)}</b> ${text(compactValue(item.content))}<small>round ${text(item.createdRound)} · ${text(item.id)}</small></li>`).join('');
    const graphEdges = (graph.edges || []).map((edge) => `<li><b>${text(edge.type || 'related_to')}</b> ${text(edge.sourceNodeId || edge.source)} → ${text(edge.targetNodeId || edge.target)}${edge.sequence ? ` · sequence ${text(edge.sequence)}` : ''}</li>`).join('');
    return `<div class="simulation-event-inspector" data-sim-event-inspector="${text(event.id)}">
      <section data-sim-event-evidence="actor"><h5>Actor</h5><p><b>${text(actor?.id || event.actorId || 'Author')}</b> · ${text(actor?.type || event.actorType || 'author')} · target ${text((event.targetIds || []).join(', ') || 'none')}</p>${actor ? `<pre>${jsonText({ before: actor.before, after: actor.after })}</pre>` : ''}</section>
      <section data-sim-event-evidence="memory"><h5>Memory</h5><ul>${memoryRows || '<li class="dim-note">No agent-local memory was persisted for this event.</li>'}</ul><small>Agent-scoped memory only · ${text(memory.evidence?.source || 'simulation_agent_memories')}</small></section>
      <section data-sim-event-evidence="context"><h5>Context</h5>${context ? `<p><b>${text(context.identity?.name || context.agentId)}</b> · ${text(context.actorType)} · pre-event sequence ${text(context.beforeEventSequence)}</p><pre>${jsonText({ currentState: context.currentState, localWorld: context.localWorld, knowledge: context.knowledge, beliefs: context.beliefs, goals: context.goals, relationships: context.relationships, observations: context.observations, recentEvents: context.recentEvents, recentMemory: context.recentMemory, availableActions: context.availableActions, worldRules: context.worldRules })}</pre>` : '<p class="dim-note">No actor context was available for this event.</p>'}</section>
      <section data-sim-event-evidence="why"><h5>Why</h5><p>${text(why.intent || why.reasoningSummary || 'No explicit intent recorded.')}</p><ul>${causes || '<li class="dim-note">No causal references recorded.</li>'}</ul><small>Persisted causal trace · canonicalMutation=false</small></section>
      <section data-sim-event-evidence="state-delta"><h5>State Delta</h5><pre>${jsonText({ changed: delta.changed, beforeStateHash: delta.beforeStateHash, afterStateHash: delta.afterStateHash, beforeEventSequence: delta.beforeEventSequence, afterEventSequence: delta.afterEventSequence })}</pre></section>
      <section data-sim-event-evidence="graph-changes"><h5>Related Graph Changes</h5><ul>${graphEdges || '<li class="dim-note">No graph edge touched this event.</li>'}</ul><small>${text(graph.evidence?.source || 'persisted_simulation_graph_projection')} · ${text((graph.nodes || []).length)} related node(s) · canonicalMutation=false</small></section>
    </div>`;
  }

  async function loadEventDetail(eventId, output) {
    if (!eventId || !output || !state.runId) return;
    const cached = state.eventDetails?.[eventId];
    if (cached) { output.innerHTML = renderEventInspector(cached); return; }
    output.innerHTML = '<p class="dim-note">Loading persisted event evidence...</p>';
    const runId = state.runId;
    try {
      const result = await api('GET', `${bookPath()}/runs/${encodeURIComponent(runId)}/events/${encodeURIComponent(eventId)}`);
      if (state.runId !== runId) return;
      state.eventDetails = { ...(state.eventDetails || {}), [eventId]: result };
      output.innerHTML = renderEventInspector(result);
    } catch (error) {
      output.innerHTML = `<p class="warn-banner">Event evidence unavailable: ${text(error.message)}</p>`;
    }
  }

  async function inspectReportEvent(eventId) {
    if (!eventId) return;
    const summary = Array.from(document.querySelectorAll('[data-sim-event-toggle]'))
      .find((item) => item.dataset.simEventToggle === eventId);
    if (!summary) { toast('The report event is not present in the current ledger.', 'warning'); return; }
    const container = summary.closest('details');
    const output = container?.querySelector('[data-sim-event-detail]');
    if (container) container.open = true;
    await loadEventDetail(eventId, output);
    summary.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  function renderCausality() {
    const traces = state.causality?.traces || [];
    if (!traces.length) return '<p class="dim-note">No persisted causal references yet. Causes are recorded when Sandbox events are written.</p>';
    return traces.map((trace) => `<article class="simulation-causal-event"><div><b>#${text(trace.sequence)}</b><span>${text(trace.eventType)}</span></div>
      <small>${text(trace.actorId || 'Author')} · ${text(trace.causedBy?.length || 0)} persisted cause(s)</small>
      <div class="simulation-causal-causes">${(trace.causedBy || []).map((cause) => `<span class="simulation-causal-cause"><b>${text(cause.causeType)}</b> ${text(cause.causeId)} · ${text(cause.relation)}</span>`).join('') || '<span class="dim-note">No supported cause evidence</span>'}</div>
    </article>`).join('') + '<small class="simulation-evidence-note">Causal trace is derived from persisted Sandbox events, memories, interventions, relationships, goals, and world rules · canonicalMutation=false</small>';
  }

  function renderInterventions() {
    if (!(state.interventions || []).length) return '<p class="dim-note">No author interventions recorded for this run.</p>';
    return state.interventions.map((item) => `<article class="simulation-intervention-item">
      <div><b>${text(item.kind)}</b><span>${text(item.author || 'author')} · ${text(item.createdAt)}</span></div>
      <p>${text(item.rationale)}</p><pre>${jsonText(item.stateDelta)}</pre>
      <small>Event ${text(item.eventId)} · intervention ${text(item.id)}</small>
    </article>`).join('') + '<small class="simulation-evidence-note">Persisted in simulation_interventions and the Sandbox event ledger · canonicalMutation=false</small>';
  }

  function characterAgents() {
    return (state.agents || []).filter((agent) => agent.type === 'character');
  }

  function renderChatHistory() {
    if (!(state.chatInteractions || []).length) return '<p class="dim-note">No persisted interaction for this character.</p>';
    return state.chatInteractions.slice().reverse().map((item) => `<article class="simulation-chat-message">
      <small>${text(item.status)} · ${text(item.createdAt)}</small><p><b>You:</b> ${text(item.prompt)}</p><p><b>Character:</b> ${text(item.response)}</p>
    </article>`).join('');
  }

  function renderSurveys() {
    if (!(state.surveys || []).length) return '<p class="dim-note">No persisted surveys yet.</p>';
    return state.surveys.map((survey) => `<article class="simulation-survey-result"><div><b>${text(survey.question)}</b><span class="${statusClass(survey.status)}">${text(survey.status)}</span></div>
      ${(survey.responses || []).map((item) => `<p><b>${text(item.agentId)}</b> · ${text(item.status)}: ${text(item.response)}</p>`).join('')}
      <button class="btn btn-ghost btn-sm" type="button" data-sim-survey-run="${text(survey.id)}">Start Simulation from this scenario</button></article>`).join('');
  }

  function renderGraph() {
    if (!state.graph) return `<p class="dim-note">Graph unavailable${state.analysisError ? `: ${text(state.analysisError)}` : '.'}</p>`;
    const nodes = state.graph.nodes || [];
    const edges = state.graph.edges || [];
    const visibleNodes = nodes.slice(0, 36);
    const width = 760;
    const columns = Math.max(1, Math.min(6, Math.ceil(Math.sqrt(Math.max(1, visibleNodes.length)))));
    const rows = Math.max(1, Math.ceil(visibleNodes.length / columns));
    const height = Math.max(240, rows * 82 + 46);
    const positions = new Map();
    visibleNodes.forEach((node, index) => {
      const column = index % columns;
      const row = Math.floor(index / columns);
      positions.set(String(node.simulationId || node.id), {
        x: ((column + 0.5) * width) / columns,
        y: 26 + ((row + 0.5) * (height - 48)) / rows,
      });
    });
    const nodeFor = (value) => {
      const raw = String(value || '');
      return positions.get(raw) || positions.get(raw.split(':').pop());
    };
    const graphEdges = edges.map((edge) => {
      const source = nodeFor(edge.sourceNodeId || edge.source);
      const target = nodeFor(edge.targetNodeId || edge.target);
      if (!source || !target) return '';
      return `<line class="simulation-graph-edge" x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}" tabindex="-1"><title>${text(edge.type || 'related_to')} · ${text(edge.sequence || '')}</title></line>`;
    }).join('');
    const graphNodes = visibleNodes.map((node) => {
      const position = positions.get(String(node.simulationId || node.id));
      const type = String(node.type || 'Node').toLowerCase();
      const id = text(node.simulationId || node.id);
      return `<g class="simulation-graph-node simulation-graph-node-${text(type)}" data-sim-graph-node="${id}" tabindex="0" role="button" aria-label="Inspect ${text(node.label || node.simulationId || node.id)}" transform="translate(${position.x} ${position.y})"><circle r="18"></circle><text y="32" text-anchor="middle">${text(String(node.label || node.simulationId || node.id).slice(0, 22))}</text><title>${text(node.label || node.simulationId || node.id)} · ${text(node.type || 'Node')}</title></g>`;
    }).join('');
    const omitted = Math.max(0, nodes.length - visibleNodes.length);
    return `<div class="simulation-graph-metrics"><span><b>${text(state.graph.evidence?.mode || 'SIMULATION')}</b> mode</span><span>run <b>${text(state.graph.evidence?.runId || state.runId)}</b></span><span>round <b>${text(state.graph.evidence?.round ?? state.detail?.run?.currentRound ?? 0)}</b></span><span><b>${text(nodes.length)}</b> nodes</span><span><b>${text(edges.length)}</b> edges</span><span>sequence <b>${text(state.graph.eventSequence)}</b></span></div>
      <div class="simulation-graph-viewport" role="img" aria-label="Persisted simulation graph"><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet"><defs><marker id="simulation-graph-arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z"></path></marker></defs><g class="simulation-graph-edges">${graphEdges}</g><g class="simulation-graph-nodes">${graphNodes}</g></svg></div>
      <div class="simulation-graph-legend"><span><i class="simulation-graph-legend-dot simulation-graph-legend-character"></i>Character</span><span><i class="simulation-graph-legend-dot simulation-graph-legend-faction"></i>Faction</span><span><i class="simulation-graph-legend-dot simulation-graph-legend-location"></i>Location</span><span><i class="simulation-graph-legend-dot simulation-graph-legend-narrative"></i>Narrative evidence</span></div>
      <div class="simulation-graph-list">${visibleNodes.map((node) => `<article><b>${text(node.label || node.simulationId)}</b><small>${text(node.type)} · ${text(node.simulationId)}</small></article>`).join('') || '<p class="dim-note">No projected nodes.</p>'}</div>
      <small class="simulation-evidence-note">${text(state.graph.evidence?.source || 'replayed simulation state')} · canonicalMutation=false${omitted ? ` · showing ${visibleNodes.length} of ${nodes.length} nodes` : ''}</small>`;
  }

  function renderReports() {
    if (!(state.reports || []).length) return '<p class="dim-note">No persisted reports for this run.</p>';
    return state.reports.map((report) => {
      const evidenceItems = Array.isArray(report.evidence?.keyEvents) && report.evidence.keyEvents.length
        ? report.evidence.keyEvents : (Array.isArray(report.evidence?.eventIds) ? report.evidence.eventIds.slice(0, 12) : []);
      const evidenceIds = evidenceItems.map((item) => typeof item === 'object' ? (item.eventId || item.id || '') : item).filter(Boolean);
      const links = evidenceIds.map((eventId) => `<button class="btn btn-ghost btn-sm" data-sim-report-event="${text(eventId)}">Inspect event ${text(eventId)}</button>`).join('');
      return `<article class="simulation-report-item"><div><b>${text(report.title)}</b><small>${text(report.kind)}</small></div><p>${text(report.summary)}</p><small>State ${text(report.evidence?.stateHash || 'recorded')} · ${text(report.createdAt)}</small>${links ? `<div class="row row-wrap simulation-report-evidence-links">${links}</div>` : '<small class="dim-note">No clickable event evidence recorded.</small>'}</article>`;
    }).join('');
  }

  function renderOutcomes() {
    const outcomes = state.outcomes || {};
    const clusters = outcomes.clusters || [];
    if (!clusters.length) {
      return '<p class="dim-note">No repeat-run outcomes yet. Duplicate this run, execute the repeats, then refresh.</p>';
    }
    return `<div class="simulation-compare-metrics"><span>Runs analyzed <b>${text(outcomes.analyzedRunIds?.length || 0)}</b></span><span>Clusters <b>${text(outcomes.clusterCount || clusters.length)}</b></span><span>Probability claims <b>none</b></span></div>
      <div class="simulation-outcome-clusters">${clusters.map((cluster) => `<article class="simulation-outcome-cluster"><div><b>${text(cluster.label)}</b><span>${text(cluster.runCount)} run${cluster.runCount === 1 ? '' : 's'}</span></div><p>Outcome <code>${text(cluster.outcomeHash)}</code></p><small>Representative ${text(cluster.representativeRunId)} · ${text(cluster.eventCount)} ledger events</small><small>Statuses: ${text(compactValue(cluster.statusCounts))}</small></article>`).join('')}</div>
      <small class="simulation-evidence-note">Exact replay-state clusters only; no probability is inferred from run counts · canonicalMutation=false</small>`;
  }

  function renderComparison() {
    const comparison = state.comparison;
    if (!comparison) return '<p class="dim-note">Choose two runs to compare persisted sandbox outcomes.</p>';
    const changed = Object.entries(comparison.changedKeys || {});
    const dimensions = Object.entries(comparison.dimensionChanges || {});
    return `<div class="simulation-compare-metrics"><span>Common sequence <b>${text(comparison.commonEventSequence)}</b></span><span>Left-only <b>${text((comparison.leftOnlyEvents || []).length)}</b></span><span>Right-only <b>${text((comparison.rightOnlyEvents || []).length)}</b></span></div>
      <p><small>Left hash:</small> ${text(comparison.leftStateHash)}</p><p><small>Right hash:</small> ${text(comparison.rightStateHash)}</p>
      <h5>Dimension changes</h5><div class="simulation-change-list">${dimensions.map(([key, value]) => `<article><b>${text(key)}</b><small>left: ${text(compactValue(value.left))}</small><small>right: ${text(compactValue(value.right))}</small></article>`).join('') || '<p class="dim-note">No listed narrative dimension changed.</p>'}</div>
      <div class="simulation-change-list">${changed.map(([key, value]) => `<article><b>${text(key)}</b><small>left: ${text(compactValue(value.left))}</small><small>right: ${text(compactValue(value.right))}</small></article>`).join('') || '<p class="dim-note">No top-level state differences.</p>'}</div>
      <small class="simulation-evidence-note">Persisted event ledger comparison · canonicalMutation=false</small>`;
  }

  function renderTask() {
    const task = state.detail?.task;
    if (!task) return '<p class="dim-note">No durable task is bound to this run. Queue a round to create recoverable work.</p>';
    const status = String(task.status || '').toLowerCase();
    const controls = [];
    if (status === 'queued') controls.push('<button class="btn btn-ghost btn-sm" data-sim-task-action="cancel">Cancel</button>');
    if (status === 'running') controls.push('<button class="btn btn-secondary btn-sm" data-sim-task-action="pause">Pause</button><button class="btn btn-ghost btn-sm" data-sim-task-action="cancel">Cancel</button>');
    if (status === 'paused') controls.push('<button class="btn btn-primary btn-sm" data-sim-task-action="resume">Resume</button><button class="btn btn-ghost btn-sm" data-sim-task-action="cancel">Cancel</button>');
    if (status === 'failed') controls.push('<button class="btn btn-secondary btn-sm" data-sim-task-action="retry">Retry</button>');
    const checkpoint = task.checkpoint || {};
    return `<div class="simulation-task-metrics"><span>Status <b class="${statusClass(task.status)}">${text(task.status)}</b></span><span>Stage <b>${text(task.stage || 'queued')}</b></span><span>Progress <b>${text(task.progressPercent ?? 0)}%</b></span></div>
      <p><small>Task ${text(task.id || task.taskId)} · ${text(task.type)} · persisted task runtime</small></p>
      ${checkpoint.id ? `<small>Checkpoint ${text(checkpoint.id)} · ${text(checkpoint.stage || 'recorded')}</small>` : '<small>No checkpoint recorded yet.</small>'}
      <div class="row row-wrap simulation-task-controls">${controls.join('') || '<small class="dim-note">No author control is available for this task state.</small>'}</div>
      <small class="simulation-evidence-note">Recovery state is read from durable storage · canonicalMutation=false</small>`;
  }

  function renderConfiguration() {
    const configuration = state.detail?.run?.configuration || {};
    const editable = ['DRAFT', 'PREPARING', 'READY', 'PAUSED', 'PAUSED_BUDGET'].includes(state.detail?.run?.status);
    const value = (key, fallback = '') => configuration[key] == null ? fallback : configuration[key];
    const structured = [
      ['Agents', value('agents', { source: 'snapshot' })],
      ['Initial location', value('initialLocation', 'snapshot-defined')],
      ['Goals', value('goals', [])],
      ['Activity', value('activity', {})],
      ['Decision frequency', value('decisionFrequency', 'per_round')],
      ['Memory policy', value('memoryPolicy', 'run_scoped')],
      ['Simulation horizon', value('simulationHorizon', state.detail?.run?.maxRounds || 'run max rounds')],
      ['Round duration', value('clock', { roundDuration: '1 day' })],
      ['Max actions / round', value('maxActionsPerRound', 1)],
      ['Narrative randomness', value('narrativeRandomness', 0)],
      ['Conflict resolution', value('conflictResolution', 'deterministic')],
      ['Provider assignment', value('providerAssignment', {})],
    ];
    return `<div class="simulation-config-summary"><div class="simulation-config-grid">${structured.map(([label, item]) => `<div><small>${text(label)}</small><b>${text(compactValue(item))}</b></div>`).join('')}</div>${editable ? '<div class="row row-wrap"><button class="btn btn-ghost btn-sm" data-sim-config-generate>Generate from immutable snapshot</button><button class="btn btn-ghost btn-sm" data-sim-config-edit>Edit environment setup</button></div>' : ''}</div><pre class="simulation-config-json">${jsonText(configuration)}</pre><small class="simulation-evidence-note">Persisted sandbox configuration · ${editable ? 'author-editable before the next round' : 'locked for this run state'} · canonicalMutation=false</small>`;
  }

  function renderScheduler() {
    const scheduler = state.scheduler;
    if (!scheduler) return '<p class="dim-note">Scheduler evidence unavailable.</p>';
    const rows = (scheduler.activations || []).map((item) => `<article class="simulation-scheduler-agent"><div><b>${text(item.agentId)}</b><span class="simulation-tier-chip">Tier ${text(item.tier)}</span><span class="${item.active ? 'simulation-scheduler-active' : 'simulation-scheduler-passive'}">${item.active ? 'ACTIVE' : 'PASSIVE'}</span></div><small>${text((item.whyActivated || item.reasons || []).join(' · ') || 'rule gate')}</small></article>`).join('');
    return `<div class="simulation-scheduler-summary"><b>${text((scheduler.activeAgents || []).length)}</b> active / ${text((scheduler.activations || []).length)} discovered</div>${rows || '<p class="dim-note">No sandbox agents discovered.</p>'}<small class="simulation-evidence-note">${text(scheduler.evidence?.source || 'deterministic scheduler')} · whyActivated · canonicalMutation=false</small>`;
  }

  function renderAgentRoster() {
    const agents = state.agents || [];
    if (!agents.length) return '<p class="dim-note">No replayed sandbox agents discovered.</p>';
    return `<div class="simulation-agent-roster">${agents.map((agent) => `<article class="simulation-agent-card">
      <button type="button" class="simulation-agent-card-main" data-sim-agent-select="${text(agent.id)}" aria-label="Inspect ${text(agent.name)}">
        <span><b>${text(agent.name)}</b><small>${text(agent.type)} · ${text(agent.id)}</small></span>
        <span class="${agent.alive === false ? 'simulation-scheduler-passive' : 'simulation-scheduler-active'}">${agent.alive === false ? 'DEAD' : 'ALIVE'}</span>
      </button>
      <div class="simulation-agent-card-meta"><span>${text(compactValue(agent.location) || 'location unknown')}</span><span>${text(compactValue(agent.goals) || 'no goals recorded')}</span></div>
      <small class="simulation-agent-card-evidence">stateHash ${text(agent.stateHash || 'not recorded')} · canonicalMutation=false</small>
    </article>`).join('')}</div><small class="simulation-evidence-note">Roster is derived from replayed SimulationWorldState; selecting an Agent loads bounded perception and memory evidence.</small>`;
  }

  function renderBudget() {
    const payload = state.budget?.budget;
    if (!payload) return '<p class="dim-note">Budget evidence unavailable.</p>';
    const usage = payload.usage || {};
    const estimate = payload.estimate || {};
    const limits = payload.limits || {};
    const status = payload.status || 'within_budget';
    return `<div class="simulation-budget-metrics"><span>Calls <b>${text(usage.calls || 0)}</b> / ${text(limits.maxGenerationCalls == null ? '∞' : limits.maxGenerationCalls)}</span><span>Tokens <b>${text(usage.tokens || 0)}</b> / ${text(limits.maxTokens == null ? '∞' : limits.maxTokens)}</span><span>Cost <b>${text(usage.cost || 0)}</b> / ${text(limits.maxCost == null ? '∞' : limits.maxCost)}</span></div><small>Next estimate: ${text(estimate.calls || 0)} calls · ${text(estimate.tokens || 0)} tokens · ${text(estimate.cost || 0)} cost</small><span class="${statusClass(status)}">${text(status)}</span>${state.detail?.run?.status === 'PAUSED_BUDGET' ? `<form data-sim-budget class="simulation-form simulation-inline-form"><label>Max calls<input class="input" type="number" min="0" name="maxGenerationCalls" value="${text(limits.maxGenerationCalls ?? '')}"></label><label>Max tokens<input class="input" type="number" min="0" name="maxTokens" value="${text(limits.maxTokens ?? '')}"></label><label>Max cost<input class="input" type="number" min="0" step="0.0001" name="maxCost" value="${text(limits.maxCost ?? '')}"></label><button class="btn btn-secondary" type="submit">Increase budget</button></form>` : ''}<small class="simulation-evidence-note">Persisted GenerationRun usage · ${text(state.budget?.ledger?.length || 0)} ledger rows · canonicalMutation=false</small>`;
  }

  function renderSnapshotProvenance() {
    const detail = state.detail;
    const snapshot = detail?.snapshot;
    if (!snapshot) return '<p class="dim-note">Snapshot provenance unavailable.</p>';
    const freshness = detail.snapshotFreshness || 'UNKNOWN';
    const current = detail.currentCanon || {};
    const changed = freshness !== 'CURRENT';
    return `<div class="simulation-provenance-status"><span class="${statusClass(freshness)}">${text(freshness)}</span><span>${changed ? 'Refresh required before relying on this run as current Canon.' : 'Snapshot still matches the current Canon boundary.'}</span></div>
      <div class="simulation-provenance-grid">
        <div><small>BASE CANON</small><b>${text(snapshot.base_canon_event_id)}</b></div>
        <div><small>EVENT</small><b>${text(snapshot.base_canon_event_id)}</b></div>
        <div><small>HASH</small><code>${text(snapshot.canon_hash)}</code></div>
        <div><small>SNAPSHOT TIME</small><b>${text(snapshot.created_at)}</b></div>
        <div><small>CURRENT EVENT</small><b>${text(current.eventId || 'unknown')}</b></div>
        <div><small>CURRENT HASH</small><code>${text(current.canonHash || 'unknown')}</code></div>
      </div>
      <small class="simulation-evidence-note">Canonical source: ${text(detail.snapshotEvidence?.canonicalSource || 'sqlite.narrative_events')} · canonicalMutation=false</small>`;
  }

  function renderWorldInventory() {
    const world = state.detail?.snapshot?.world || {};
    const collections = [
      ['characters', 'Characters'], ['factions', 'Factions'], ['locations', 'Locations'],
      ['relationships', 'Relationships'], ['timeline', 'Timeline'], ['foreshadows', 'Foreshadows'],
      ['plot_threads', 'Plot threads'], ['secrets', 'Secrets'], ['story_goals', 'Story goals'],
      ['conflicts', 'Conflicts'], ['items', 'Items'], ['known_facts', 'Known facts'],
      ['narrative_obligations', 'Narrative obligations'], ['world_rules', 'World rules'],
      ['power_systems', 'Power systems'],
    ];
    const count = (value) => Array.isArray(value) ? value.length
      : (value && typeof value === 'object' ? Object.keys(value).length : (value ? 1 : 0));
    const summary = collections.map(([key, label]) => `<span class="simulation-world-count"><b>${text(count(world[key]))}</b><small>${text(label)}</small></span>`).join('');
    const entries = [];
    collections.forEach(([key, label]) => {
      const value = world[key];
      const values = Array.isArray(value) ? value.map((item, index) => [index, item])
        : (value && typeof value === 'object' ? Object.entries(value) : []);
      values.slice(0, 8).forEach(([index, item]) => {
        const record = item && typeof item === 'object' ? item : { value: item };
        const id = record.id || record.key || index;
        const labelValue = record.name || record.title || record.rule_text || record.content || record.goal || id;
        entries.push(`<li><b>${text(labelValue)}</b><small>${text(label)} 路 ${text(id)}</small></li>`);
      });
    });
    return `<div class="simulation-world-counts">${summary}</div><ul class="simulation-world-entities">${entries.slice(0, 40).join('') || '<li class="dim-note">No additional snapshot entities recorded.</li>'}</ul><small class="simulation-evidence-note">Read-only inventory from immutable snapshot.world 路 canonicalMutation=false</small>`;
  }

  function nextStatus(run) {
    if (!run) return null;
    if (run.status === 'DRAFT') return { status: 'READY', label: 'Prepare run' };
    if (run.status === 'PREPARING') return { status: 'READY', label: 'Mark ready' };
    if (run.status === 'READY') return { status: 'RUNNING', label: 'Start run' };
    if (run.status === 'RUNNING') return { status: 'PAUSED', label: 'Pause run' };
    if (run.status === 'PAUSED') return { status: 'RUNNING', label: 'Resume run' };
    if (run.status === 'PAUSED_BUDGET') return { status: 'RUNNING', label: 'Resume after budget' };
    return null;
  }

  function renderDetail() {
    const detail = state.detail;
    if (!detail) {
      return '<section class="simulation-workspace-main"><div class="simulation-empty"><strong>Select or create a simulation run</strong><span>Simulation state remains isolated from Canon until explicit author adoption.</span></div></section>';
    }
    const run = detail.run;
    const transition = nextStatus(run);
    const workspace = activeWorkspace();
    return `<section class="simulation-workspace-main" data-sim-active-workspace="${workspace}">
      <div class="simulation-run-header">
        <div><h3>${text(run.name)}</h3><p>${text(run.purpose || run.description || 'Sandbox scenario')}</p></div>
        <div class="simulation-run-actions">
          <span class="${statusClass(run.status)}">${text(run.status)}</span>
          ${transition ? `<button class="btn btn-primary" data-sim-transition="${transition.status}">${transition.label}</button>` : ''}
           <button class="btn btn-secondary" data-sim-branch>Fork branch</button>
           <button class="btn btn-ghost" data-sim-replicate>Duplicate repeat</button>
           ${['DRAFT', 'PREPARING', 'READY', 'RUNNING', 'PAUSED', 'PAUSED_BUDGET', 'FAILED'].includes(run.status) ? '<button class="btn btn-ghost" data-sim-stop>Stop run</button>' : ''}
           ${detail.history?.deleted ? '<span class="simulation-evidence-note">Deleted from History; evidence retained</span>' : (detail.history?.archived ? '<button class="btn btn-ghost" data-sim-unarchive>Unarchive</button>' : '<button class="btn btn-ghost" data-sim-archive>Archive</button>')}
          ${detail.history?.deleted ? '' : '<button class="btn btn-ghost" data-sim-delete>Delete from History</button>'}
        </div>
      </div>
      <div class="simulation-metrics" aria-label="Simulation evidence">
        <div><small>Round</small><b>${text(run.currentRound)} / ${text(run.maxRounds)}</b></div>
        <div><small>Ledger events</small><b data-sim-event-sequence>${text(detail.eventSequence)}</b></div>
        <div><small>Seed</small><b>${text(run.seed)}</b></div>
        <div><small>Canon</small><b>Read only</b></div>
      </div>
      ${renderWorkspaceNav()}
      <div class="simulation-grid">
        <section class="simulation-panel simulation-branch-tree-panel" data-sim-workspaces="history"><div class="simulation-panel-heading"><h4>Branch Tree</h4><button class="btn btn-ghost btn-sm" data-sim-refresh>Refresh</button></div>${renderBranchTree()}</section>
        <section class="simulation-panel simulation-provenance-panel" data-sim-workspaces="world"><div class="simulation-panel-heading"><h4>World Snapshot provenance</h4><small>Immutable Canon boundary</small></div>${renderSnapshotProvenance()}<h5 class="simulation-subheading">World entity inventory</h5>${renderWorldInventory()}</section>
        <section class="simulation-panel simulation-task-panel" data-sim-workspaces="simulate history"><div class="simulation-panel-heading"><h4>Durable round task</h4><button class="btn btn-ghost btn-sm" data-sim-refresh>Refresh</button></div>${renderTask()}</section>
        <section class="simulation-panel" data-sim-workspaces="world simulate"><div class="simulation-panel-heading"><h4>Environment configuration</h4><small>Run-scoped, detached from Canon</small></div>${renderConfiguration()}</section>
        <section class="simulation-panel simulation-agent-roster-panel" data-sim-workspaces="agents"><div class="simulation-panel-heading"><h4>Agent roster</h4><small>Character and Faction entities replayed into this Sandbox</small></div>${renderAgentRoster()}</section>
        <section class="simulation-panel" data-sim-workspaces="agents simulate"><div class="simulation-panel-heading"><h4>Agent Scheduler</h4><small>Tiered activation with whyActivated evidence</small></div>${renderScheduler()}</section>
        <section class="simulation-panel" data-sim-workspaces="simulate"><div class="simulation-panel-heading"><h4>Token / cost control</h4><small>Provider usage and author-controlled pause</small></div>${renderBudget()}</section>
        <section class="simulation-panel simulation-timeline-panel" data-sim-workspaces="simulate history"><div class="simulation-panel-heading"><h4>Event timeline</h4><button class="btn btn-ghost btn-sm" data-sim-refresh>Refresh</button></div><div data-sim-timeline-body>${renderTimeline()}</div></section>
        <section class="simulation-panel simulation-causal-panel" data-sim-workspaces="analyze"><div class="simulation-panel-heading"><h4>Narrative causal trace</h4><small>Why an event occurred, from persisted Sandbox evidence</small></div>${renderCausality()}</section>
        <section class="simulation-panel" data-sim-workspaces="simulate">
          <div class="simulation-panel-heading"><h4>Author intervention</h4><small>Writes an explicit sandbox event</small></div>
          <form data-sim-intervention class="simulation-form">
            <label>Kind<input class="input" name="kind" required value="WORLD_VARIABLE"></label>
            <label>Reason<textarea class="ta" name="rationale" required placeholder="Why this counterfactual change is being applied"></textarea></label>
            <label>State delta (JSON)<textarea class="ta" name="delta" required placeholder='{"weather":"storm"}'>{}</textarea></label>
            <button class="btn btn-secondary" type="submit">Apply intervention</button>
          </form>
        </section>
        <section class="simulation-panel" data-sim-workspaces="simulate history"><div class="simulation-panel-heading"><h4>Intervention history</h4><button class="btn btn-ghost btn-sm" data-sim-refresh>Refresh</button></div>${renderInterventions()}</section>
        <section class="simulation-panel" data-sim-workspaces="agents simulate"><div class="simulation-panel-heading"><h4>Run next round</h4><small>Typed action · validator · append-only ledger</small></div>
          <form data-sim-round class="simulation-form">
            <div class="simulation-form-grid"><label>Decision mode<select class="input" name="decisionMode"><option value="explicit" selected>Explicit author action</option><option value="provider">Provider Agent decision</option></select></label><label>Decision role<select class="input" name="decisionRole"><option value="planner" selected>Planner</option><option value="writer">Writer</option><option value="reviewer">Reviewer</option></select></label></div>
            <div class="simulation-form-grid"><label>Agent<select class="input" name="actorId" required>${(state.agents || []).map((agent) => `<option value="${text(agent.id)}" data-agent-type="${text(agent.type)}">${text(agent.name)} · ${text(agent.type)} · ${text(agent.id)}</option>`).join('')}</select></label><label>Action<select class="input" name="actionType">${renderActionOptions()}</select></label></div>
            <div class="simulation-provider-agent-picker" data-sim-provider-agent-picker>${renderProviderAgentOptions()}</div>
            <div class="simulation-agent-evidence" data-sim-agent-evidence>Select an Agent to inspect its local state.</div>
            <label>Intent<input class="input" name="intent" placeholder="What is this agent trying to do?"></label>
            <label>Effects (JSON)<textarea class="ta" name="effects" required>{}</textarea></label>
            <div class="row row-wrap"><button class="btn btn-primary" type="submit" data-sim-round-mode="execute">Execute explicit round</button><button class="btn btn-secondary" type="submit" data-sim-round-mode="queue">Queue durable round</button></div>
          </form>
        </section>
        <section class="simulation-panel" data-sim-workspaces="analyze history"><div class="simulation-panel-heading"><h4>Evidence analysis</h4><small>Deterministic ledger report</small></div>
          <form data-sim-analysis class="simulation-form simulation-inline-form"><label>Report title<input class="input" name="title" placeholder="Run summary"></label><button class="btn btn-secondary" type="submit">Create report</button></form>
          <form data-sim-analyst-query class="simulation-form"><label>Ask Analyst<textarea class="ta" name="question" required placeholder="Which persisted events changed the relationship?"></textarea></label><label>Tool (optional)<input class="input" name="tool" placeholder="query_simulation_events"></label><button class="btn btn-secondary" type="submit">Ask grounded Analyst</button></form>
          <div id="simulation-analysis-result" class="simulation-analysis-result"></div><div id="simulation-analyst-query-result" class="simulation-analysis-result"></div><div class="simulation-report-history">${renderReports()}</div>
        </section>
        <section class="simulation-panel" data-sim-workspaces="analyze history"><div class="simulation-panel-heading"><h4>Outcome clusters</h4><small>Repeated runs · exact persisted state only</small><button class="btn btn-ghost btn-sm" data-sim-outcomes-refresh>Refresh</button></div>
          <div data-sim-outcomes>${renderOutcomes()}</div>
        </section>
        <section class="simulation-panel" data-sim-workspaces="world analyze"><div class="simulation-panel-heading"><h4>Simulation graph</h4><button class="btn btn-ghost btn-sm" data-sim-graph-refresh>Refresh</button></div><small>Read-only projection from state and ledger</small>
          <div class="simulation-graph-result">${renderGraph()}</div>
        </section>
        <section class="simulation-panel" data-sim-workspaces="analyze history"><div class="simulation-panel-heading"><h4>Compare runs</h4><small>Persisted counterfactual outcomes</small></div>
          <form data-sim-compare class="simulation-form"><div class="simulation-form-grid"><label>Left run<select class="input" name="leftRun" required>${(state.runs || []).map((item) => `<option value="${text(item.id)}" ${item.id === state.runId ? 'selected' : ''}>${text(item.name)} · ${text(item.status)}</option>`).join('')}</select></label><label>Right run<select class="input" name="rightRun" required>${(state.runs || []).map((item, index) => `<option value="${text(item.id)}" ${item.id !== state.runId && index === 1 ? 'selected' : ''}>${text(item.name)} · ${text(item.status)}</option>`).join('')}</select></label></div><button class="btn btn-secondary" type="submit">Compare outcomes</button></form>
          <div class="simulation-comparison-result" data-sim-comparison-result>${renderComparison()}</div>
        </section>
        <section class="simulation-panel" data-sim-workspaces="interact"><div class="simulation-panel-heading"><h4>Character interaction</h4><small>Agent-local perception and memory only</small></div>
          <form data-sim-chat class="simulation-form"><label>Character<select class="input" name="agentId" required>${characterAgents().map((agent) => `<option value="${text(agent.id)}" ${agent.id === state.chatAgentId ? 'selected' : ''}>${text(agent.name)} · ${text(agent.id)}</option>`).join('')}</select></label>
            <label>Question<textarea class="ta" name="prompt" required placeholder="Ask what this character knows or remembers"></textarea></label><button class="btn btn-secondary" type="submit">Ask character</button></form>
          <div class="simulation-chat-history" data-sim-chat-history>${state.interactionError ? `<p class="dim-note">History unavailable: ${text(state.interactionError)}</p>` : renderChatHistory()}</div>
        </section>
        <section class="simulation-panel" data-sim-workspaces="interact history"><div class="simulation-panel-heading"><h4>Multi-agent survey</h4><small>Each response stays scoped to its selected Character or Faction Agent</small></div>
          <form data-sim-survey class="simulation-form"><label>Question<textarea class="ta" name="question" required placeholder="Ask the same question to selected characters"></textarea></label>
            <div class="simulation-agent-checks">${(state.agents || []).map((agent) => `<label class="simulation-agent-check"><input type="checkbox" name="agentIds" value="${text(agent.id)}" checked><span>${text(agent.name)} · ${text(agent.type)}</span></label>`).join('')}</div>
            <button class="btn btn-secondary" type="submit">Run survey</button></form>
          <div class="simulation-survey-history" data-sim-survey-history>${state.interactionError ? `<p class="dim-note">History unavailable: ${text(state.interactionError)}</p>` : renderSurveys()}</div>
        </section>
        <section class="simulation-panel" data-sim-workspaces="history analyze"><div class="simulation-panel-heading"><h4>Author adoption</h4><small>Simulation result → Planning proposal</small></div>
          <form data-sim-adoption class="simulation-form"><label>Proposal title<input class="input" name="title" required placeholder="A consequence worth planning"></label><label>Summary<textarea class="ta" name="summary" required placeholder="Describe the outcome to carry into planning"></textarea></label><button class="btn btn-secondary" type="submit">Create adoption proposal</button></form>
          <div id="simulation-adoption-result" class="simulation-analysis-result">${renderProposals()}</div>
        </section>
      </div>
    </section>`;
  }

  function render() {
    const page = document.getElementById('page');
    if (!page) return;
    if (!S.book) {
      page.innerHTML = header('Narrative Simulation', 'Select a book first') + '<div class="content"><div class="simulation-empty"><strong>A book is required</strong><button class="btn btn-primary" data-sim-dashboard>Open books</button></div></div>';
      page.querySelector('[data-sim-dashboard]')?.addEventListener('click', () => go('dashboard'));
      return;
    }
    page.innerHTML = header('Narrative Simulation', 'Canon-bound snapshots · isolated counterfactual runtime · explicit author adoption', '<button class="btn btn-secondary" data-sim-new>New simulation</button>') + `
      <div class="simulation-workspace">
        <aside class="simulation-run-list"><div class="simulation-list-heading"><h3>Runs</h3><button class="btn btn-ghost btn-sm" data-sim-refresh>Refresh</button></div>${state.loading ? '<div class="simulation-empty simulation-empty-compact">Loading runs...</div>' : renderRuns()}</aside>
        ${state.error ? `<section class="simulation-workspace-main"><div class="warn-banner">${text(state.error)}</div></section>` : renderDetail()}
      </div>`;
    bind();
  }

  function bind() {
    document.querySelectorAll('[data-sim-refresh]').forEach((button) => button.addEventListener('click', refresh));
    const timeline = document.querySelector('[data-sim-timeline-body]');
    timeline?.addEventListener('click', (event) => {
      const summary = event.target.closest('[data-sim-event-toggle]');
      if (!summary || !timeline.contains(summary)) return;
      const eventId = summary.dataset.simEventToggle;
      // A run switch is asynchronous.  Ignore a click from the previous
      // run's DOM until the new persisted ledger has rendered.
      if (!(state.events || []).some((item) => item.id === eventId)) return;
      const output = summary.closest('[data-sim-event]')?.querySelector('[data-sim-event-detail]')
        || summary.parentElement?.querySelector('[data-sim-event-detail]');
      window.queueMicrotask(() => loadEventDetail(eventId, output));
    });
    document.querySelectorAll('[data-sim-workspace]').forEach((button) => button.addEventListener('click', () => {
      state.workspace = button.dataset.simWorkspace;
      try { window.localStorage.setItem(workspaceStorageKey(), state.workspace); } catch (_) { /* optional */ }
      render();
    }));
    document.querySelectorAll('[data-sim-run]').forEach((button) => button.addEventListener('click', async () => {
      state.runId = button.dataset.simRun;
      state.detail = null;
      state.events = [];
      state.eventDetails = {};
      await refresh();
    }));
    document.querySelector('[data-sim-new]')?.addEventListener('click', createRun);
     document.querySelector('[data-sim-transition]')?.addEventListener('click', transitionRun);
     document.querySelector('[data-sim-stop]')?.addEventListener('click', stopRun);
    document.querySelector('[data-sim-branch]')?.addEventListener('click', branchRun);
    document.querySelector('[data-sim-intervention]')?.addEventListener('submit', intervene);
    document.querySelector('[data-sim-analysis]')?.addEventListener('submit', analyze);
    document.querySelector('[data-sim-analyst-query]')?.addEventListener('submit', askAnalyst);
    document.querySelector('[data-sim-compare]')?.addEventListener('submit', compareRuns);
    document.querySelector('[data-sim-outcomes-refresh]')?.addEventListener('click', refreshOutcomes);
    document.querySelector('[data-sim-replicate]')?.addEventListener('click', replicateRun);
     document.querySelector('[data-sim-archive]')?.addEventListener('click', () => changeArchiveState('archive'));
     document.querySelector('[data-sim-unarchive]')?.addEventListener('click', () => changeArchiveState('unarchive'));
     document.querySelector('[data-sim-delete]')?.addEventListener('click', deleteRun);
     document.querySelector('[data-sim-graph-refresh]')?.addEventListener('click', refreshGraph);
    document.querySelector('[data-sim-chat]')?.addEventListener('submit', chat);
    document.querySelector('[data-sim-chat] select[name="agentId"]')?.addEventListener('change', switchChatAgent);
    document.querySelector('[data-sim-survey]')?.addEventListener('submit', survey);
    document.querySelectorAll('[data-sim-survey-run]').forEach((button) => button.addEventListener('click', () => startSurveyScenario(button.dataset.simSurveyRun)));
    document.querySelector('[data-sim-round]')?.addEventListener('submit', executeRound);
    document.querySelectorAll('[data-sim-task-action]').forEach((button) => button.addEventListener('click', controlSimulationTask));
    document.querySelector('[data-sim-adoption]')?.addEventListener('submit', proposeAdoption);
     document.querySelector('[data-sim-budget]')?.addEventListener('submit', updateBudget);
     document.querySelector('[data-sim-config-edit]')?.addEventListener('click', editConfiguration);
     document.querySelector('[data-sim-config-generate]')?.addEventListener('click', generateConfiguration);
     bindReportEvidence();
     document.querySelectorAll('[data-sim-adopt]').forEach((button) => button.addEventListener('click', () => adoptProposal(button.dataset.simAdopt)));
    document.querySelectorAll('[data-sim-edit]').forEach((button) => button.addEventListener('click', () => {
      const card = button.closest('[data-sim-proposal-card]');
      const proposal = (state.proposals || []).find((item) => item.id === button.dataset.simEdit);
      editProposal(
        button.dataset.simEdit,
        card?.querySelector('[data-sim-proposal-title]')?.value,
        card?.querySelector('[data-sim-proposal-summary]')?.value,
        proposal?.payload,
      );
    }));
    document.querySelectorAll('[data-sim-reject]').forEach((button) => button.addEventListener('click', () => rejectProposal(button.dataset.simReject)));
    document.querySelectorAll('[data-sim-intent]').forEach((button) => button.addEventListener('click', () => {
      const row = button.closest('.simulation-intent-row');
      createChapterIntent(button.dataset.simIntent, row?.querySelector('input[name="chapterNumber"]')?.value);
    }));
    document.querySelectorAll('[data-sim-write]').forEach((button) => button.addEventListener('click', () => queueWritingTask(button.dataset.simWrite, button.parentElement.querySelector('input[name="chapterNumber"]')?.value)));
    document.querySelectorAll('[data-sim-write-retry]').forEach((button) => button.addEventListener('click', () => retryWritingTask(button.dataset.simWriteRetry)));
    document.querySelector('[data-sim-round] select[name="actorId"]')?.addEventListener('change', inspectSelectedAgent);
    document.querySelector('[data-sim-round] select[name="decisionMode"]')?.addEventListener('change', updateRoundMode);
    document.querySelectorAll('[data-sim-agent-select]').forEach((button) => button.addEventListener('click', () => {
      const select = document.querySelector('[data-sim-round] select[name="actorId"]');
      if (!select) return;
      select.value = button.dataset.simAgentSelect;
      inspectSelectedAgent();
    }));
    document.querySelectorAll('[data-sim-graph-node]').forEach((node) => node.addEventListener('click', () => {
      const select = document.querySelector('[data-sim-round] select[name="actorId"]');
      if (!select || ![...select.options].some((option) => option.value === node.dataset.simGraphNode)) return;
      select.value = node.dataset.simGraphNode;
      inspectSelectedAgent();
    }));
    inspectSelectedAgent();
    updateRoundMode();
  }

  function updateRoundMode() {
    const form = document.querySelector('[data-sim-round]');
    if (!form) return;
    const provider = form.elements.decisionMode?.value === 'provider';
    const execute = form.querySelector('[data-sim-round-mode="execute"]');
    const queue = form.querySelector('[data-sim-round-mode="queue"]');
    if (execute) {
      execute.disabled = provider;
      execute.textContent = provider ? 'Provider requires durable task' : 'Execute explicit round';
    }
    if (queue) queue.textContent = provider ? 'Queue provider round' : 'Queue durable round';
    const providerAgents = form.elements.providerAgentIds;
    if (providerAgents) providerAgents.disabled = !provider;
  }

  function bindReportEvidence() {
    document.querySelectorAll('[data-sim-report-event]').forEach((button) => {
      if (button.dataset.simReportBound === 'true') return;
      button.dataset.simReportBound = 'true';
      button.addEventListener('click', () => inspectReportEvent(button.dataset.simReportEvent));
    });
  }

  function renderProposals() {
    if (!(state.proposals || []).length) return '<p class="dim-note">No adoption proposals yet.</p>';
    return (state.proposals || []).map((proposal) => {
      const id = text(proposal.id);
      let controls = '';
      if (proposal.status === 'PROPOSED') {
        controls = `<div class="simulation-proposal-edit"><label>Title<input class="input" data-sim-proposal-title value="${text(proposal.title)}"></label><label>Summary<textarea class="ta" data-sim-proposal-summary>${text(proposal.summary)}</textarea></label><div class="row"><button class="btn btn-secondary btn-sm" data-sim-adopt="${id}">Adopt into Planning</button><button class="btn btn-ghost btn-sm" data-sim-edit="${id}">Save edit</button><button class="btn btn-ghost btn-sm" data-sim-reject="${id}">Reject</button></div></div>`;
      } else if (proposal.status === 'ADOPTED') {
        const persistedIntents = Array.isArray(proposal.chapterIntents) ? proposal.chapterIntents : [];
        const intentEvidence = persistedIntents.map((intent) => `<small class="simulation-intent-evidence">ChapterIntent ${text(intent.chapter_number || intent.chapterNumber)} · ${text(intent.status || 'PLANNED')} · persisted author overlay</small>`).join('');
        const writingTasks = Array.isArray(proposal.writingTasks) ? proposal.writingTasks : [];
        const writingEvidence = writingTasks.map((task) => {
          const taskStatus = String(task.status || 'queued');
          const terminalFailure = ['failed', 'needs_author_decision'].includes(taskStatus);
          const errorEvidence = task.error
            ? ` · ${terminalFailure ? 'error' : 'last error'}: ${text(task.error)}`
            : '';
          const retryAction = terminalFailure
            ? ` <button class="btn btn-ghost btn-sm" data-sim-write-retry="${text(task.id)}">Retry</button>`
            : '';
          return `<small class="simulation-intent-evidence">write-next ${text(taskStatus)} · task ${text(task.id)} · ${text(task.progressPercent ?? 0)}%${errorEvidence}${retryAction}</small>`;
        }).join('');
        controls = `<small>Planning node: ${text(proposal.planningNodeId || 'recorded')}</small>${intentEvidence}${writingEvidence}<div class="row simulation-intent-row"><input class="input" type="number" min="1" name="chapterNumber" value="${text(state.detail?.nextChapter || 1)}" aria-label="Chapter number"><button class="btn btn-secondary btn-sm" data-sim-intent="${id}">Create ChapterIntent</button><button class="btn btn-ghost btn-sm" data-sim-write="${id}">Queue write-next</button></div>`;
      } else {
        controls = '<small class="dim-note">Rejected; no Planning node was created.</small>';
      }
      return `<article class="simulation-proposal" data-sim-proposal-card><div><b>${text(proposal.title)}</b><span class="${statusClass(proposal.status)}">${text(proposal.status)}</span></div><p>${text(proposal.summary)}</p><small>${id}</small>${controls}</article>`;
    }).join('');
  }

  function createRun() {
    document.body.insertAdjacentHTML('beforeend', `<div class="modal-overlay" id="simulation-create-modal" role="dialog" aria-modal="true" aria-labelledby="simulation-create-title">
      <div class="modal simulation-create-modal"><div class="modal-header"><div><h3 id="simulation-create-title">New simulation run</h3><p class="dim-note">The run starts from a detached Canon snapshot.</p></div><button class="close-x" type="button" data-sim-create-close aria-label="Close">×</button></div>
      <form data-sim-create-form class="simulation-form"><label>Name<input class="input" name="name" required autofocus placeholder="e.g. The storm arrives"></label><label>Maximum rounds<input class="input" name="maxRounds" type="number" min="1" max="1000" value="3" required></label><label>Purpose<textarea class="ta" name="purpose" placeholder="What counterfactual are you testing?"></textarea></label><label>Environment configuration (JSON)<textarea class="ta" name="configuration" required>{"agents":{"source":"snapshot"},"clock":{"roundDuration":"1 day"},"decisionFrequency":"per_round","memoryPolicy":"run_scoped","communicationRules":{},"worldRules":{},"maxActionsPerRound":1,"narrativeRandomness":0,"conflictResolution":"deterministic","providerAssignment":{}}</textarea></label><div class="row"><span class="spacer"></span><button class="btn btn-secondary" type="button" data-sim-create-close>Cancel</button><button class="btn btn-primary" type="submit">Create run</button></div></form></div></div>`);
    const overlay = document.getElementById('simulation-create-modal');
    overlay.querySelector('[data-sim-create-close]').focus();
    overlay.querySelectorAll('[data-sim-create-close]').forEach((button) => button.addEventListener('click', () => overlay.remove()));
    overlay.querySelector('[data-sim-create-form]').addEventListener('submit', submitCreateRun);
  }

  function editConfiguration() {
    if (!state.detail?.run) return;
    const configuration = state.detail.run.configuration || {};
    const jsonValue = (key, fallback) => text(JSON.stringify(configuration[key] == null ? fallback : configuration[key], null, 2));
    document.body.insertAdjacentHTML('beforeend', `<div class="modal-overlay" id="simulation-config-modal" role="dialog" aria-modal="true" aria-labelledby="simulation-config-title">
      <div class="modal simulation-create-modal"><div class="modal-header"><div><h3 id="simulation-config-title">Edit environment setup</h3><p class="dim-note">Run-scoped configuration; Canon remains read only.</p></div><button class="close-x" type="button" data-sim-config-close aria-label="Close">×</button></div>
      <form data-sim-config-form class="simulation-form"><div class="simulation-form-grid"><label>Initial location<input class="input" name="initialLocation" value="${text(configuration.initialLocation || '')}" placeholder="snapshot-defined"></label><label>Decision frequency<select class="input" name="decisionFrequency"><option value="per_round" ${configuration.decisionFrequency === 'per_round' || !configuration.decisionFrequency ? 'selected' : ''}>per round</option><option value="event_driven" ${configuration.decisionFrequency === 'event_driven' ? 'selected' : ''}>event driven</option><option value="on_activation" ${configuration.decisionFrequency === 'on_activation' ? 'selected' : ''}>on activation</option></select></label><label>Memory policy<select class="input" name="memoryPolicy"><option value="run_scoped" ${configuration.memoryPolicy === 'run_scoped' || !configuration.memoryPolicy ? 'selected' : ''}>run scoped</option><option value="episodic_plus_semantic" ${configuration.memoryPolicy === 'episodic_plus_semantic' ? 'selected' : ''}>episodic + semantic</option><option value="episodic_only" ${configuration.memoryPolicy === 'episodic_only' ? 'selected' : ''}>episodic only</option></select></label><label>Round duration<input class="input" name="roundDuration" value="${text(configuration.clock?.roundDuration || '1 day')}"></label><label>Max actions / round<input class="input" type="number" min="1" max="100" name="maxActionsPerRound" value="${text(configuration.maxActionsPerRound ?? 1)}"></label><label>Narrative randomness<input class="input" type="number" min="0" max="1" step="0.01" name="narrativeRandomness" value="${text(configuration.narrativeRandomness ?? 0)}"></label><label>Conflict resolution<select class="input" name="conflictResolution"><option value="deterministic" ${configuration.conflictResolution !== 'priority' ? 'selected' : ''}>deterministic</option><option value="priority" ${configuration.conflictResolution === 'priority' ? 'selected' : ''}>priority</option></select></label></div><label>Agents / tier policies (JSON)<textarea class="ta" name="agents" required>${jsonValue('agents', { source: 'snapshot' })}</textarea></label><label>Goals (JSON)<textarea class="ta" name="goals" required>${jsonValue('goals', [])}</textarea></label><label>Activity (JSON)<textarea class="ta" name="activity" required>${jsonValue('activity', {})}</textarea></label><label>Communication rules (JSON)<textarea class="ta" name="communicationRules" required>${jsonValue('communicationRules', {})}</textarea></label><label>World rules (JSON)<textarea class="ta" name="worldRules" required>${jsonValue('worldRules', {})}</textarea></label><label>Provider / model assignment (JSON)<textarea class="ta" name="providerAssignment" required>${jsonValue('providerAssignment', {})}</textarea></label><div class="row"><span class="spacer"></span><button class="btn btn-secondary" type="button" data-sim-config-close>Cancel</button><button class="btn btn-primary" type="submit">Save environment setup</button></div></form></div></div>`);
    const overlay = document.getElementById('simulation-config-modal');
    overlay.querySelectorAll('[data-sim-config-close]').forEach((button) => button.addEventListener('click', () => overlay.remove()));
    overlay.querySelector('[data-sim-config-form]').addEventListener('submit', submitConfiguration);
  }

  async function submitConfiguration(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const parse = (name, fallback) => {
      try {
        const value = JSON.parse(form.elements[name].value || 'null');
        return value == null ? fallback : value;
      } catch (_) { throw new Error(`${name} must be valid JSON.`); }
    };
    let configuration;
    try {
      const existing = { ...(state.detail?.run?.configuration || {}) };
      configuration = {
        ...existing,
        agents: existing.agents || { source: 'snapshot' },
        initialLocation: form.initialLocation.value.trim() || null,
        decisionFrequency: form.decisionFrequency.value,
        memoryPolicy: form.memoryPolicy.value,
        clock: { ...(existing.clock || {}), roundDuration: form.roundDuration.value.trim() || '1 day' },
        maxActionsPerRound: Number(form.maxActionsPerRound.value),
        narrativeRandomness: Number(form.narrativeRandomness.value),
        conflictResolution: form.conflictResolution.value,
        agents: parse('agents', { source: 'snapshot' }), goals: parse('goals', []), activity: parse('activity', {}),
        communicationRules: parse('communicationRules', {}),
        worldRules: parse('worldRules', {}), providerAssignment: parse('providerAssignment', {}),
      };
      if (!Number.isInteger(configuration.maxActionsPerRound) || configuration.maxActionsPerRound < 1 || configuration.maxActionsPerRound > 100) throw new Error('Max actions / round must be between 1 and 100.');
      if (!Number.isFinite(configuration.narrativeRandomness) || configuration.narrativeRandomness < 0 || configuration.narrativeRandomness > 1) throw new Error('Narrative randomness must be between 0 and 1.');
    } catch (error) { toast(error.message, 'warning'); return; }
    try {
      await api('POST', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/configuration`, { configuration, replace: true });
      document.getElementById('simulation-config-modal')?.remove();
      toast('Environment setup persisted for this Sandbox run.', 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function submitCreateRun(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const name = form.name.value.trim();
    const maxRounds = Number(form.maxRounds.value);
    if (!name || !Number.isInteger(maxRounds) || maxRounds < 1 || maxRounds > 1000) {
      toast('Enter a name and a maximum between 1 and 1000 rounds.', 'warning');
      return;
    }
    let configuration;
    try { configuration = JSON.parse(form.configuration.value || '{}'); } catch (_) { toast('Environment configuration must be valid JSON.', 'warning'); return; }
    if (!configuration || Array.isArray(configuration) || typeof configuration !== 'object') {
      toast('Environment configuration must be a JSON object.', 'warning');
      return;
    }
    try {
      const snapshot = await api('POST', `${bookPath()}/snapshots`, {});
      const result = await api('POST', `${bookPath()}/runs`, { snapshotId: snapshot.snapshotId, name, maxRounds, purpose: form.purpose.value.trim(), configuration });
      state.runId = result.runId;
      document.getElementById('simulation-create-modal')?.remove();
      toast('Simulation run created from an immutable world snapshot.', 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function transitionRun() {
    const status = document.querySelector('[data-sim-transition]')?.dataset.simTransition;
    if (!status || !state.runId) return;
    try {
      await api('POST', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/status`, { status });
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function stopRun() {
    if (!state.runId) return;
    if (typeof window.confirm === 'function' && !window.confirm('Stop this Simulation run? Its Sandbox evidence will remain available.')) return;
    try {
      await api('POST', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/status`, { status: 'CANCELLED' });
      toast('Simulation stopped; Sandbox evidence was retained.', 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function updateBudget(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const body = {};
    ['maxGenerationCalls', 'maxTokens', 'maxCost'].forEach((key) => {
      const value = form.elements[key]?.value;
      if (value !== '') body[key] = key === 'maxCost' ? Number(value) : Number.parseInt(value, 10);
    });
    if (!Object.keys(body).length) { toast('Enter at least one budget limit.', 'warning'); return; }
    try {
      await api('POST', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/budget`, body);
      toast('Simulation budget updated; resume only when the limits are sufficient.', 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function branchRun() {
    if (!state.detail) return;
    document.body.insertAdjacentHTML('beforeend', `<div class="modal-overlay" id="simulation-branch-modal" role="dialog" aria-modal="true" aria-labelledby="simulation-branch-title"><div class="modal simulation-create-modal"><div class="modal-header"><div><h3 id="simulation-branch-title">Fork simulation branch</h3><p class="dim-note">The parent ledger remains unchanged.</p></div><button class="close-x" type="button" data-sim-branch-close aria-label="Close">×</button></div><form data-sim-branch-form class="simulation-form"><label>Branch name<input class="input" name="name" required value="${text(`${state.detail.run.name} branch`)}"></label><div class="row"><span class="spacer"></span><button class="btn btn-secondary" type="button" data-sim-branch-close>Cancel</button><button class="btn btn-primary" type="submit">Fork branch</button></div></form></div></div>`);
    const overlay = document.getElementById('simulation-branch-modal');
    overlay.querySelectorAll('[data-sim-branch-close]').forEach((button) => button.addEventListener('click', () => overlay.remove()));
    overlay.querySelector('[data-sim-branch-form]').addEventListener('submit', submitBranchRun);
  }

  async function submitBranchRun(event) {
    event.preventDefault();
    const name = event.currentTarget.name.value.trim();
    if (!name) return;
    try {
      const result = await api('POST', `${bookPath()}/branches`, {
        parentRunId: state.runId, forkSequence: state.detail.eventSequence, name: name.trim(),
      });
      state.runId = result.runId;
      document.getElementById('simulation-branch-modal')?.remove();
      toast('Branch created as an isolated simulation run.', 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function intervene(event) {
    event.preventDefault();
    const form = event.currentTarget;
    let stateDelta;
    try { stateDelta = JSON.parse(form.delta.value); } catch (_) { toast('State delta must be valid JSON.', 'warning'); return; }
    try {
      await api('POST', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/interventions`, {
        kind: form.kind.value.trim(), rationale: form.rationale.value.trim(), stateDelta,
      });
      toast('Sandbox intervention persisted.', 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function executeRound(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const submitter = event.submitter;
    const decisionMode = form.decisionMode?.value || 'explicit';
    const decisionRole = form.decisionRole?.value || 'planner';
    let effects;
    try { effects = JSON.parse(form.effects.value); } catch (_) { toast('Effects must be valid JSON.', 'warning'); return; }
    const action = {
      actionType: form.actionType.value, actorId: form.actorId.value.trim(), actorType: form.actorId.selectedOptions[0]?.dataset.agentType || 'character', intent: form.intent.value.trim(), effects,
    };
    if (!action.actorId) { toast('Actor ID is required.', 'warning'); return; }
    const providerMode = decisionMode === 'provider';
    const queued = providerMode || submitter?.dataset.simRoundMode === 'queue';
    const suffix = queued ? '/round-tasks' : '/rounds';
    const selectedProviderAgents = providerMode
      ? [...(form.elements.providerAgentIds?.selectedOptions || [])].map((option) => option.value).filter(Boolean)
      : [];
    try {
      const result = await api('POST', `${bookPath()}/runs/${encodeURIComponent(state.runId)}${suffix}`, {
        actions: providerMode ? [] : [action],
        decisionMode,
        decisionRole,
        agentIds: selectedProviderAgents,
      });
      toast(queued ? `${providerMode ? 'Provider round' : 'Round'} task queued: ${result.taskId}` : `Round ${result.roundNumber} persisted.`, 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function controlSimulationTask(event) {
    const action = event.currentTarget.dataset.simTaskAction;
    const taskId = state.detail?.task?.id || state.detail?.task?.taskId;
    if (!action || !taskId) return;
    try {
      await api('POST', `/tasks/${encodeURIComponent(taskId)}/${action}`, {});
      toast(`Durable task ${action} request persisted.`, 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function inspectSelectedAgent() {
    const select = document.querySelector('[data-sim-round] select[name="actorId"]');
    const output = document.querySelector('[data-sim-agent-evidence]');
    if (!select || !output || !select.value) return;
    const agent = (state.agents || []).find((item) => item.id === select.value);
    if (agent) output.innerHTML = `<b>${text(agent.name)}</b> · ${text(agent.type)} · ${text(compactValue(agent.location) || 'location unknown')}<br><small>Goals: ${text(compactValue(agent.goals) || 'none recorded')} · sandbox-local evidence</small>`;
    try {
      const result = await api('GET', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/agents/${encodeURIComponent(select.value)}?event_limit=5`);
      output.innerHTML = `<b>${text(result.perception.identity?.name || agent?.name || select.value)}</b> · ${text(agent?.type || 'character')} · ${text(result.perception.currentState?.location || result.perception.currentState?.territory || 'location unknown')}<br><small>Visible events: ${text(result.perception.recentEvents?.length || 0)} · Memories: ${text(result.perception.recentMemory?.length || 0)} · local perception only</small>`;
    } catch (_) { /* The roster remains usable if an individual inspector is unavailable. */ }
  }

  async function analyze(event) {
    event.preventDefault();
    const output = document.getElementById('simulation-analysis-result');
    try {
      const result = await api('POST', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/analysis`, {
        kind: 'run-summary', title: event.currentTarget.elements.title.value.trim() || null,
      });
      const report = result.report;
      state.reports = [report, ...(state.reports || []).filter((item) => item.id !== report.id)];
      output.innerHTML = `<p><b>${text(report.title)}</b></p><p>${text(report.summary || 'Evidence report created.')}</p><small>State hash ${text(report.evidence?.stateHash || 'recorded')}</small>`;
      const history = document.querySelector('.simulation-report-history');
      if (history) {
        history.innerHTML = renderReports();
        bindReportEvidence();
      }
    } catch (error) { toast(error.message, 'error'); }
  }

  async function askAnalyst(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const question = form.question.value.trim();
    if (!question) { toast('Enter a question for the Analyst.', 'warning'); return; }
    const output = document.getElementById('simulation-analyst-query-result');
    try {
      const body = { question };
      if (form.tool.value.trim()) body.tool = form.tool.value.trim();
      const result = await api('POST', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/analysis/query`, body);
      const analysis = result.analysis || {};
      output.innerHTML = `<p><b>${text(analysis.answer || analysis.summary || 'Grounded analysis recorded.')}</b></p><small>Grounded: ${text(analysis.grounded)} · task ${text(result.taskId || 'recorded')}</small><pre class="simulation-query-evidence">${text(JSON.stringify(analysis.evidenceChain || [], null, 2))}</pre>`;
      form.question.value = '';
      toast(`Analyst answer persisted (task ${result.taskId || 'recorded'}).`, 'success');
    } catch (error) { toast(error.message, 'error'); }
  }

  async function compareRuns(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const left = form.leftRun.value;
    const right = form.rightRun.value;
    if (!left || !right || left === right) { toast('Choose two different runs to compare.', 'warning'); return; }
    try {
      state.comparison = await api('GET', `${bookPath()}/compare?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`);
      const output = document.querySelector('[data-sim-comparison-result]');
      if (output) output.innerHTML = renderComparison();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function refreshOutcomes() {
    if (!state.runId) return;
    try {
      state.outcomes = await api('GET', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/outcomes`);
      const output = document.querySelector('[data-sim-outcomes]');
      if (output) output.innerHTML = renderOutcomes();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function replicateRun() {
    if (!state.runId) return;
    const raw = window.prompt('How many repeat runs should be created? (1-20)', '2');
    if (raw == null) return;
    const count = Number(raw);
    if (!Number.isInteger(count) || count < 1 || count > 20) {
      toast('Repeat count must be an integer between 1 and 20.', 'warning');
      return;
    }
    try {
      const result = await api('POST', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/replicate`, { count });
      toast(`Created ${result.runIds.length} repeat run(s) in cohort ${result.cohortId}.`, 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function changeArchiveState(action) {
    if (!state.runId) return;
    const reason = window.prompt(action === 'archive' ? 'Reason for archiving this Sandbox run (optional)' : 'Reason for restoring this run (optional)', '') ?? '';
    try {
      await api('POST', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/${action}`, { reason });
      toast(action === 'archive' ? 'Simulation archived without deleting Sandbox evidence.' : 'Simulation restored to History.', 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function deleteRun() {
    if (!state.runId) return;
    const reason = window.prompt('Reason for deleting this run from History (evidence is retained)', '') ?? '';
    try {
      await api('DELETE', `${bookPath()}/runs/${encodeURIComponent(state.runId)}`, { reason });
      toast('Simulation removed from History; Sandbox evidence was retained.', 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function generateConfiguration() {
    if (!state.runId) return;
    try {
      await api('POST', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/configuration/generate`, { replace: true });
      toast('Environment setup generated from the immutable snapshot.', 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function refreshGraph(silent = false) {
    if (!state.runId) return;
    try {
      state.graph = await api('GET', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/graph?event_limit=1000`);
      state.analysisError = '';
      const output = document.querySelector('.simulation-graph-result');
      if (output) output.innerHTML = renderGraph();
    } catch (error) {
      state.analysisError = error.message;
      const output = document.querySelector('.simulation-graph-result');
      if (output) output.innerHTML = renderGraph();
      if (!silent) toast(error.message, 'error');
    }
  }

  async function switchChatAgent(event) {
    state.chatAgentId = event.currentTarget.value;
    try {
      const result = await api('GET', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/agents/${encodeURIComponent(state.chatAgentId)}/chat?limit=50`);
      state.chatInteractions = result.interactions || [];
      const history = document.querySelector('[data-sim-chat-history]');
      if (history) history.innerHTML = renderChatHistory();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function chat(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const agentId = form.agentId.value;
    const prompt = form.prompt.value.trim();
    if (!agentId || !prompt) { toast('Select a character and enter a question.', 'warning'); return; }
    try {
      const result = await api('POST', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/agents/${encodeURIComponent(agentId)}/chat`, { prompt });
      form.prompt.value = '';
      state.chatAgentId = agentId;
      await refresh();
      toast(`Character interaction persisted (task ${result.taskId || 'recorded'}).`, 'success');
    } catch (error) { toast(error.message, 'error'); }
  }

  async function survey(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const question = form.question.value.trim();
    const agentIds = Array.from(form.querySelectorAll('input[name="agentIds"]:checked')).map((input) => input.value);
    if (!question || !agentIds.length) { toast('Enter a question and select at least one character.', 'warning'); return; }
    try {
      const result = await api('POST', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/survey`, { question, agentIds });
      form.question.value = '';
      await refresh();
      toast(`Survey responses persisted (task ${result.taskId || 'recorded'}).`, 'success');
    } catch (error) { toast(error.message, 'error'); }
  }

  async function startSurveyScenario(surveyId) {
    if (!surveyId || !state.runId) return;
    const name = window.prompt('Name for the new survey scenario run (optional)', '') ?? '';
    try {
      const result = await api('POST', `${bookPath()}/surveys/${encodeURIComponent(surveyId)}/run`, {
        name: name.trim() || null,
        configuration: { decisionFrequency: 'per_round', surveyScenarioSource: surveyId },
      });
      state.runId = result.runId;
      toast('Survey scenario forked into a new READY Sandbox run.', 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function proposeAdoption(event) {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      await api('POST', `${bookPath()}/runs/${encodeURIComponent(state.runId)}/adoptions`, {
        title: form.title.value.trim(), summary: form.summary.value.trim(), payload: { source: 'simulation-workspace' },
      });
      form.reset();
      await refresh();
      toast('Adoption proposal persisted; Canon is unchanged.', 'success');
    } catch (error) { toast(error.message, 'error'); }
  }

  async function adoptProposal(proposalId) {
    try {
      await api('POST', `${bookPath()}/adoptions/${encodeURIComponent(proposalId)}/adopt`, {});
      toast('Proposal adopted into the revisioned Planning overlay.', 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function editProposal(proposalId, title, summary, existingPayload) {
    const cleanTitle = String(title || '').trim();
    if (!cleanTitle) { toast('Proposal title is required.', 'warning'); return; }
    const payload = existingPayload && typeof existingPayload === 'object' && !Array.isArray(existingPayload)
      ? existingPayload
      : { source: 'simulation-workspace' };
    try {
      await api('POST', `${bookPath()}/adoptions/${encodeURIComponent(proposalId)}/edit`, {
        title: cleanTitle, summary: String(summary || ''), payload,
      });
      toast('Proposal edit persisted.', 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function rejectProposal(proposalId) {
    try {
      await api('POST', `${bookPath()}/adoptions/${encodeURIComponent(proposalId)}/reject`, {});
      toast('Proposal rejected; no Planning node was created.', 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function createChapterIntent(proposalId, chapterNumber) {
    const chapter = Number(chapterNumber);
    if (!Number.isInteger(chapter) || chapter < 1) { toast('Chapter number must be at least 1.', 'warning'); return; }
    try {
      await api('POST', `${bookPath()}/adoptions/${encodeURIComponent(proposalId)}/chapter-intent`, { chapterNumber: chapter });
      toast(`ChapterIntent created for chapter ${chapter}.`, 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function queueWritingTask(proposalId, chapterNumber) {
    const chapter = Number(chapterNumber);
    if (!Number.isInteger(chapter) || chapter < 1) { toast('Chapter number must be at least 1.', 'warning'); return; }
    try {
      const result = await api('POST', `${bookPath()}/adoptions/${encodeURIComponent(proposalId)}/writing-task`, {
        chapterNumber: chapter, context: 'Simulation adoption handoff', count: 1,
      });
      const status = String(result.status || 'queued');
      toast(`Durable write-next task ${status}: ${result.taskId}`, status === 'queued' ? 'success' : 'warning');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function retryWritingTask(taskId) {
    try {
      const result = await api('POST', `/tasks/${encodeURIComponent(taskId)}/retry`, {});
      toast(`Durable write-next retry ${result.status || 'queued'}: ${taskId}`, 'success');
      await refresh();
    } catch (error) { toast(error.message, 'error'); }
  }

  PAGES.simulation = async function simulationPage() {
    if (state?.eventSource) state.eventSource.close();
    state = { runs: [], branchTree: { nodes: [], edges: [] }, workspace: initialWorkspace(), runId: '', detail: null, events: [], eventDetails: {}, agents: [], proposals: [], interventions: [], surveys: [], chatInteractions: [], chatAgentId: '', interactionError: '', graph: null, scheduler: null, budget: null, causality: null, reports: [], outcomes: null, comparison: null, analysisError: '', loading: true, error: '' };
    render();
    await refresh();
  };
  if (typeof renderNav === 'function') renderNav();
  if (typeof S !== 'undefined' && S.page === 'simulation' && S.book) {
    // Enter through the real router; no timer drives or represents simulation
    // progress.  The backend EventSource and durable task state are the only
    // runtime clocks exposed by this workspace.
    go('simulation');
  }
}());
