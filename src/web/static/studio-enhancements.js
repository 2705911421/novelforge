(function () {
  'use strict';

  if (window.__novelforgeStudioEnhancements) return;
  window.__novelforgeStudioEnhancements = true;

  const addNav = (item) => {
    if (item.id === 'extension-home' || item.id === 'agent-config') return;
    if (item.bookOnly && !item.navGroup) item.navGroup = '更多功能';
    if (!NAV.some((entry) => entry.id === item.id && entry.id)) NAV.push(item);
  };

  addNav({ id: 'themes', label: '人物主题', icon: 'M4 4h16v16H4z M8 8h8 M8 12h5 M8 16h8', bookOnly: true });
  addNav({ id: 'flow', label: '交互式关系图', icon: 'M6 4a2 2 0 100 4 2 2 0 000-4 M18 10a2 2 0 100 4 2 2 0 000-4 M8 7l8 5', bookOnly: true });
  addNav({ id: 'planning', label: '规划总览', navGroup: '规划与结构', icon: 'M4 5h16v14H4z M8 9h8 M8 13h5', bookOnly: true });
  addNav({ id: 'thought', label: '念头创作', icon: 'M12 3a7 7 0 00-4 12c1 1 1 2 1 3h6c0-1 0-2 1-3a7 7 0 00-4-12z M9 21h6', bookOnly: true });
  addNav({ id: 'project-settings', label: '作品设置', icon: 'M12 15a3 3 0 100-6 3 3 0 000 6z M19.4 15a1.65 1.65 0 00.33 1.82l.06.06', bookOnly: true });

  addNav({ id: 'film', label: '互动影像工作台', icon: 'M3 5h18v14H3z M7 5v14 M17 5v14', bookOnly: true });
  addNav({ id: 'film-flow', label: '互动影像流程', icon: 'M5 6h5v5H5z M14 13h5v5h-5z M10 8h4v8h-4z', bookOnly: true });
  addNav({ id: 'story-player', label: 'StoryPlayer', icon: 'M8 5v14l11-7z', bookOnly: true });
  addNav({ id: 'cover', label: '封面生成', icon: 'M4 5h16v14H4z M8 15l3-4 2 2 2-3 3 5', bookOnly: true });

  const stringify = (value) => JSON.stringify(value == null ? {} : value, null, 2);
  const pretty = (value) => esc(typeof value === 'string' ? value : stringify(value));
  const taskError = (task) => task?.error || task?.checkpoint?.state?.message || (typeof TASK_ERROR_LABELS === 'object' && TASK_ERROR_LABELS[task?.error_code]) || task?.status || '任务未完成';
  const readableProjection = (value) => typeof readableTaskRows === 'function' ? readableTaskRows(value) : `<p class="text-sm" style="white-space:pre-wrap">${esc(taskValueText(value))}</p>`;
  const activeStatus = new Set(['queued', 'running', 'waiting_on_child', 'paused', 'cancelling']);

  function isMissingFeatureError(error) {
    return error?.status === 404 || /not found|不存在|NOT_FOUND/i.test(String(error?.message || ''));
  }

  function renderFeatureEmpty(target, title, message, action) {
    if (!target) return;
    target.innerHTML = header(title, bookName(), '') + `<div class="content"><div class="empty-state feature-empty-state"><div class="feature-empty-mark" aria-hidden="true">+</div><h2>还没有可用资产</h2><p>${esc(message)}</p><div class="row row-wrap mt16">${action || ''}<button class="btn btn-ghost" onclick="go('book')">返回作品概览</button></div></div></div>`;
  }

  async function hasInteractiveFilm(projectId) {
    const data = await api('GET', '/interactive-films');
    return (data.films || []).some((item) => item.projectId === projectId);
  }

  function renderTaskState(target, task, label) {
    if (!target) return;
    if (window.modelWork && task?.id) window.modelWork.attachTask(task.id, label || task?.displayName || task?.type, task);
    target.innerHTML = `<div class="card"><div class="row"><b>${esc(label || task?.type || '任务')}</b><span class="spacer"></span>${statusBadge(task?.status)}</div>
      <p class="dim-note mt8">阶段：${esc(typeof taskStageLabel === 'function' ? taskStageLabel(task?.stage, task?.status, task?.workflowState) : (task?.stage || 'queued'))} · ${esc(taskError(task))}</p>
      ${window.modelWork ? window.modelWork.renderInline(task, label || task?.displayName || task?.type, { compact: true }) : `<div class="progress mt8"><div class="progress-bar" style="width:${Math.max(0, Math.min(100, Number(task?.progressPercent ?? task?.progress) || 0))}%"></div></div>`}
      ${task?.result ? (typeof readableTaskResult === 'function' ? readableTaskResult(task.result, '执行结果') : `<details class="mt8"><summary>执行结果</summary><p class="text-sm" style="white-space:pre-wrap;margin-top:8px">${esc(taskValueText(task.result))}</p></details>`) : ''}
    </div>`;
  }

  function pollEnhancedTask(taskId, target, label, onComplete) {
    if (!taskId) return;
    let stopped = false;
    const loop = async () => {
      if (stopped) return;
      try {
        const task = await api('GET', '/tasks/' + encodeURIComponent(taskId));
        if (window.modelWork) window.modelWork.attachTask(taskId, label || task?.displayName || task?.type, task);
        renderTaskState(target, task, label);
        if (activeStatus.has(task.status)) {
          window.setTimeout(loop, 1200);
        } else if (task.status === 'completed') {
          onComplete?.(task);
        } else {
          onComplete?.(task);
        }
      } catch (error) {
        if (!stopped) {
          if (target) target.innerHTML = `<div class="warn-banner">无法读取任务 ${esc(taskId)}：${esc(error.message)}</div>`;
          window.setTimeout(loop, 2500);
        }
      }
    };
    loop();
    return () => { stopped = true; };
  }

  function ensureBookList() {
    if (S.books?.length) return Promise.resolve(S.books);
    return api('GET', '/books').then((data) => {
      S.books = data.books || [];
      return S.books;
    });
  }

  // The old page used window.open and received JSON for this route. The Studio
  // route now returns a real attachment, so download it as a browser file.
  window.doDownload = async function (id) {
    const format = document.getElementById('ex-fmt')?.value || 'md';
    const approved = Boolean(document.getElementById('ex-approved')?.checked);
    if (!['md', 'txt', 'docx'].includes(format)) {
      toast('请选择纯文本或 Word 文档格式。', 'warning');
      return;
    }
    try {
      const response = await fetch(`/api/v1/books/${encodeURIComponent(id)}/export?format=${encodeURIComponent(format)}&approvedOnly=${approved}`);
      if (!response.ok) {
        const detail = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(detail.detail || detail.message || response.statusText);
      }
      const blob = await response.blob();
      const href = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = href;
      anchor.download = response.headers.get('content-disposition')?.match(/filename="?([^";]+)"?/i)?.[1] || `${bookName()}.${format}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(href);
      toast('文件下载已开始', 'success');
    } catch (error) {
      toast(error.message, 'error');
    }
  };

  // ========== Chat sessions ==========
  let chatSessionId = localStorage.getItem('novelforge-chat-session-' + (S.book || 'global')) || '';
  let chatMode = localStorage.getItem('novelforge-chat-mode') || '';
  let chatSkillIds = [];
  try { chatSkillIds = JSON.parse(localStorage.getItem('novelforge-chat-skills') || '[]'); } catch (_) { chatSkillIds = []; }
  if (!Array.isArray(chatSkillIds)) chatSkillIds = [];
  const chatModes = { '': '通用助手', thought: '念头创作', short: '短篇小说', script: '剧本', storyboard: '分镜', 'interactive-film': '互动影像', 'play-guided': '分支互动', 'play-open': '开放互动', fanfic: '同人创作', spinoff: '衍生创作', imitation: '风格研究', 'cover-brief': '封面策划' };
  window.setChatMode = async function (mode) {
    chatMode = mode || '';
    localStorage.setItem('novelforge-chat-mode', chatMode);
    await render();
  };
  window.selectChatSession = async function (sessionId) {
    chatSessionId = sessionId;
    localStorage.setItem('novelforge-chat-session-' + (S.book || 'global'), sessionId);
    await render();
  };
  window.newChatSession = async function () {
    chatSessionId = '';
    localStorage.removeItem('novelforge-chat-session-' + (S.book || 'global'));
    await render();
  };
  window.setChatSkills = function () {
    chatSkillIds = [...document.querySelectorAll('input[name="chat-skill"]:checked')].map((item) => item.value);
    localStorage.setItem('novelforge-chat-skills', JSON.stringify(chatSkillIds));
  };
  window.sendChat = async function () {
    const input = document.getElementById('chat-input');
    const message = input?.value.trim();
    if (!message) return;
    const log = document.getElementById('chat-log');
    const append = (role, content) => {
      if (!log) return;
      const row = document.createElement('div');
      row.style.cssText = 'margin:10px 0;padding:10px 14px;border-radius:10px;line-height:1.7;' + (role === 'user' ? 'background:var(--primary-dim);border:1px solid rgba(233,69,96,.25)' : 'background:var(--bg);border:1px solid var(--border)');
      row.innerHTML = `<div class="text-sm" style="color:var(--text-dim);margin-bottom:4px">${role === 'user' ? '你' : '助手'}</div><div>${esc(content)}</div>`;
      log.appendChild(row);
      log.scrollTop = log.scrollHeight;
      return row;
    };
    append('user', message);
    input.value = '';
    const button = document.getElementById('chat-send');
    if (button) button.disabled = true;
    const pending = append('assistant', '思考中…');
    try {
      const result = await api('POST', '/chat', { message, bookId: S.book || '', sessionId: chatSessionId, mode: chatMode, skillIds: chatSkillIds });
      chatSessionId = result.sessionId || chatSessionId;
      localStorage.setItem('novelforge-chat-session-' + (S.book || 'global'), chatSessionId);
      if (pending) pending.querySelector('div:last-child').textContent = result.reply || '（无回复）';
    } catch (error) {
      if (pending) pending.querySelector('div:last-child').textContent = '错误：' + error.message;
    } finally {
      if (button) button.disabled = false;
    }
  };
  window.quickChat = function (message) {
    const input = document.getElementById('chat-input');
    if (input) { input.value = message; window.sendChat(); }
  };
  PAGES.chat = async (p) => {
    let sessionsData = { sessions: [] };
    let history = null;
    let skillsData = { skills: [] };
    try {
      sessionsData = await api('GET', '/chat/sessions?bookId=' + encodeURIComponent(S.book || ''));
      if (chatSessionId) history = await api('GET', '/chat/sessions/' + encodeURIComponent(chatSessionId) + '?bookId=' + encodeURIComponent(S.book || ''));
    } catch (error) {
      chatSessionId = '';
    }
    try { skillsData = await api('GET', '/skills?enabled_only=true&bookId=' + encodeURIComponent(S.book || '')); } catch (_) { skillsData = { skills: [] }; }
    if (history?.mode) chatMode = history.mode;
    const chatSkillRows = (skillsData.skills || []).map((skill) => `<label class="chat-skill-option"><input type="checkbox" name="chat-skill" value="${escAttr(skill.id)}" ${chatSkillIds.includes(skill.id) ? 'checked' : ''} onchange="setChatSkills()"><span><b>${esc(skill.name)}</b><small>${esc(skill.description || '为本次对话补充一组创作方法。')}</small></span></label>`).join('') || '<p class="dim-note">暂无已启用 Skill。</p>';
    p.innerHTML = header('AI 助手', '持久化会话、作品上下文和可恢复的创作讨论', `<select class="input" style="width:140px" onchange="setChatMode(this.value)">${Object.entries(chatModes).map(([id, label]) => `<option value="${escAttr(id)}" ${id === chatMode ? 'selected' : ''}>${esc(label)}</option>`).join('')}</select><button class="btn btn-secondary" onclick="newChatSession()">新会话</button>`) + `<div class="content"><div class="editor-layout">
      <div><div id="chat-log" class="card" style="min-height:320px;max-height:58vh;overflow-y:auto">
        ${(history?.messages || []).map((item) => `<div style="margin:10px 0;padding:10px 14px;border-radius:10px;line-height:1.7;${item.role === 'user' ? 'background:var(--primary-dim);border:1px solid rgba(233,69,96,.25)' : 'background:var(--bg);border:1px solid var(--border)'}"><div class="text-sm" style="color:var(--text-dim);margin-bottom:4px">${item.role === 'user' ? '你' : '助手'}</div><div>${esc(item.content)}</div></div>`).join('') || `<p class="muted">${S.book ? `当前作品：<b>${esc(bookName())}</b>。会话会保存到本地项目目录。` : '未选择作品；这是一个全局会话。'}</p>`}
      </div><div class="row" style="margin-top:12px;align-items:flex-end"><textarea class="input textarea" id="chat-input" style="min-height:60px" placeholder="输入问题或创作指令，Enter 发送，Shift+Enter 换行"></textarea><button class="btn btn-primary" id="chat-send" onclick="sendChat()">发送</button></div></div>
      <div class="chat-sidebar"><div class="card"><div class="card-title-row"><h3>会话</h3><span class="badge badge-muted">${sessionsData.count || 0}</span></div>
        ${(sessionsData.sessions || []).map((session) => `<button class="btn btn-ghost" style="width:100%;justify-content:flex-start;text-align:left;margin-bottom:4px;${session.id === chatSessionId ? 'background:var(--primary-dim);color:var(--primary)' : ''}" onclick="selectChatSession('${escAttr(session.id)}')"><span class="mono">${esc((session.preview || '新会话').slice(0, 34))}</span><span class="badge badge-muted" style="margin-left:5px">${esc(chatModes[session.mode || ''] || session.mode || '通用')}</span><span class="spacer"></span><span class="text-sm text-muted">${session.messageCount || 0}</span></button>`).join('') || '<p class="dim-note">还没有已保存会话。</p>'}
      </div><div class="card mt16 chat-skill-panel"><div class="card-title-row"><div><h3>本次对话的创作方法</h3><p class="dim-note">勾选后，只对当前聊天请求附加对应方法。</p></div><span class="badge badge-muted">${(skillsData.skills || []).length}</span></div><div class="chat-skill-list">${chatSkillRows}</div></div><div class="card mt16"><h3>会话保存</h3><p class="dim-note">刷新页面或重新打开 Studio 后，会话从本地项目目录恢复；模型失败时不会伪造回复。</p></div></div></div></div>`;
    const input = document.getElementById('chat-input');
    input?.addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); window.sendChat(); } });
  };

  // ========== 25-step Story Bible wizard ==========
  let wizardSelectedStep = '';
  window.selectWizardStep = function (stepKey) {
    wizardSelectedStep = stepKey;
    const data = window.__wizardData;
    const step = data?.bible?.steps?.find((item) => item.step_key === stepKey);
    const editor = document.getElementById('wizard-editor');
    if (!step || !editor) return;
    editor.value = typeof wizardDraftText === 'function' ? wizardDraftText(step.draft || {}) : String(step.draft || '');
    const suggestion = document.getElementById('wizard-suggestion');
    if (suggestion) suggestion.innerHTML = step.suggestion == null ? '<p class="dim-note">暂无 AI 建议。</p>' : `<p class="text-sm" style="white-space:pre-wrap;line-height:1.7">${esc(wizardDraftText(step.suggestion))}</p>`;
    const title = document.getElementById('wizard-selected-title');
    if (title) title.textContent = `第 ${step.step_number} 步 · ${step.step_key}`;
  };
  async function refreshWizard() {
    if (S.page === 'wizard') await render();
  }
  window.saveWizardStep = async function () {
    if (!wizardSelectedStep) return toast('请选择一个步骤', 'warning');
    const raw = document.getElementById('wizard-editor')?.value.trim();
    if (!raw) return toast('步骤内容不能为空', 'error');
    let draft;
    try { draft = JSON.parse(raw); } catch (_) { draft = { raw }; }
    try {
      await api('POST', `/books/${S.book}/wizard/steps/${encodeURIComponent(wizardSelectedStep)}`, { draft, source: 'author' });
      toast('草稿已持久化', 'success');
      await refreshWizard();
    } catch (error) { toast(error.message, 'error'); }
  };
  window.generateWizardStep = async function () {
    if (!wizardSelectedStep) return toast('请选择一个步骤', 'warning');
    const brief = document.getElementById('wizard-brief')?.value || '';
    const result = document.getElementById('wizard-task');
    if (result) result.innerHTML = '<div class="loading"><div class="spinner"></div>正在请求真实模型建议…</div>';
    try {
      const response = await api('POST', `/books/${S.book}/wizard/steps/${encodeURIComponent(wizardSelectedStep)}/generate`, { brief });
      if (result) result.innerHTML = `<div class="card"><b>建议已保存</b><p class="text-sm" style="white-space:pre-wrap;line-height:1.7;margin-top:8px">${esc(wizardDraftText(response.suggestion))}</p></div>`;
      await refreshWizard();
    } catch (error) { if (result) result.innerHTML = `<div class="warn-banner">${esc(error.message)}</div>`; }
  };
  window.confirmWizardStep = async function () {
    if (!wizardSelectedStep) return toast('请选择一个步骤', 'warning');
    try {
      await api('POST', `/books/${S.book}/wizard/steps/${encodeURIComponent(wizardSelectedStep)}/confirm`);
      toast('步骤已确认', 'success');
      await refreshWizard();
    } catch (error) { toast(error.message, 'error'); }
  };
  window.publishWizard = async function () {
    try { await api('POST', `/books/${S.book}/wizard/publish`); toast('Story Bible 已发布到作品真相边界', 'success'); await refreshWizard(); }
    catch (error) { toast(error.message, 'error'); }
  };
  // The main Studio page owns the beginner-friendly wizard. Keep the older
  // JSON editor reachable only for compatibility, but do not let the
  // enhancement bundle replace the guided experience after it loads.
  PAGES.legacyWizard = async (p) => {
    if (!S.book) return go('dashboard');
    const [state, bible] = await Promise.all([api('GET', `/books/${S.book}/wizard/state`), api('GET', `/books/${S.book}/story-bible`)]);
    window.__wizardData = { state, bible };
    const steps = bible.steps || [];
    if (!wizardSelectedStep || !steps.some((step) => step.step_key === wizardSelectedStep)) {
      wizardSelectedStep = steps.find((step) => step.status !== 'confirmed')?.step_key || steps[steps.length - 1]?.step_key;
    }
    const confirmed = steps.filter((step) => step.status === 'confirmed').length;
    const selected = steps.find((step) => step.step_key === wizardSelectedStep) || steps[0];
    p.innerHTML = header('Story Bible 25 步向导', `${esc(bookName())} · 已确认 ${confirmed}/25`, `<button class="btn btn-secondary" onclick="go('truth')">查看真相文件</button>`) + `<div class="content"><div class="warn-banner">每一步都先保存草稿，再按顺序确认；刷新或重启后会从数据库恢复。只有 25 步全部确认后才能发布。</div>
      <div class="prog"><div class="prog-bar" style="width:${Math.round(confirmed / 25 * 100)}%"></div></div><div class="dim-note mb16">当前步骤：${state.current_step > 25 ? '已完成全部步骤' : state.current_step}</div>
      <div class="editor-layout"><div class="card" style="max-height:64vh;overflow:auto"><h3>步骤列表</h3>${steps.map((step) => `<button class="btn btn-ghost" style="width:100%;justify-content:flex-start;text-align:left;margin-bottom:3px;${step.step_key === wizardSelectedStep ? 'background:var(--primary-dim);color:var(--primary)' : ''}" onclick="selectWizardStep('${escAttr(step.step_key)}')"><span class="badge ${step.status === 'confirmed' ? 'badge-success' : step.draft && Object.keys(step.draft).length ? 'badge-warning' : 'badge-muted'}">${step.step_number}</span><span style="margin-left:8px">${esc(step.step_key)}</span><span class="spacer"></span>${step.status === 'confirmed' ? '✓' : ''}</button>`).join('')}</div>
        <div><div class="card"><h3 id="wizard-selected-title">第 ${selected?.step_number || ''} 步 · ${esc(selected?.step_key || '')}</h3><textarea class="input textarea" id="wizard-editor" style="min-height:300px">${esc(wizardDraftText(selected?.draft || {}))}</textarea><div class="field mt16"><label>AI 建议的额外要求</label><input class="input" id="wizard-brief" placeholder="可选：希望更具体、偏暗黑、加强因果链…"></div><div class="row row-wrap"><button class="btn btn-secondary" onclick="saveWizardStep()">保存草稿</button><button class="btn btn-secondary" onclick="generateWizardStep()">生成建议</button><button class="btn btn-primary" onclick="confirmWizardStep()" ${selected?.status === 'confirmed' ? 'disabled' : ''}>确认此步</button>${confirmed === 25 ? '<button class="btn btn-primary" onclick="publishWizard()">发布 Story Bible</button>' : ''}</div></div><div id="wizard-suggestion" class="card"><h3>AI 建议</h3>${selected?.suggestion == null ? '<p class="dim-note">暂无 AI 建议。</p>' : `<p class="text-sm" style="white-space:pre-wrap;line-height:1.7">${esc(wizardDraftText(selected.suggestion))}</p>`}</div><div id="wizard-task"></div></div></div></div>`;
  };

  // ========== Durable forecast ==========
  function renderForecastBranches(target, task) {
    if (!target) return;
    if (task.status !== 'completed') {
      target.innerHTML = `<div class="warn-banner">预测未完成：${esc(taskError(task))}<br><span class="dim-note">任务 ${esc(task.id)} 仍可在任务管理中恢复或重试。</span></div>`;
      return;
    }
    const branches = task.result?.branches || [];
    target.innerHTML = `<div class="card-title" style="margin:16px 0 12px">推演结果：${branches.length} 个分支 · 第 ${task.result?.currentChapter ?? '—'} 章 · 深度 ${task.result?.depth ?? '—'}</div>${branches.map((branch, index) => `<div class="card" style="border-left:3px solid ${['var(--primary)','var(--accent)','var(--success)','var(--warning)','var(--error)'][index % 5]}"><div class="card-title-row"><h3>${index + 1}. ${esc(branch.title || branch.id)}</h3><span class="badge ${Number(branch.score) >= 80 ? 'badge-success' : Number(branch.score) >= 60 ? 'badge-warning' : 'badge-error'}">评分 ${esc(branch.score)}</span></div><p class="dim-note mb16">${esc(branch.summary || '')}</p><div>${(branch.plot_points || []).map((point, step) => `<div class="list-row"><span class="badge badge-muted">${step + 1}</span><span>${esc(point)}</span></div>`).join('')}</div>${(branch.risks || []).length ? `<div class="warn-banner mt8">风险：${esc(branch.risks.join('；'))}</div>` : ''}${branch.narrative ? `<p style="margin-top:12px;line-height:1.8">${esc(branch.narrative)}</p>` : ''}</div>`).join('') || '<div class="card"><p class="dim-note">模型没有返回分支，任务结果已保留在任务详情中。</p></div>'}`;
  }
  window.runForecast = async function () {
    const target = document.getElementById('forecast-result');
    const button = document.getElementById('forecast-run-btn');
    const branchCount = Number(document.getElementById('fc-branches')?.value || 3);
    const currentChapter = Number(document.getElementById('fc-chapter')?.value || 0);
    const depth = Number(document.getElementById('fc-depth')?.value || 3);
    const context = document.getElementById('fc-context')?.value || '';
    if (!Number.isInteger(branchCount) || branchCount < 1 || branchCount > 8) return toast('分支数必须在 1 到 8 之间', 'error');
    if (button) button.disabled = true;
    try {
      const queued = await api('POST', `/books/${S.book}/forecast`, { branchCount, currentChapter, depth, context });
      const task = await waitForTask(queued.taskId, (current) => renderTaskState(target, current, '剧情推演'));
      renderForecastBranches(target, task);
    } catch (error) {
      if (target) target.innerHTML = `<div class="warn-banner">剧情推演失败：${esc(error.message)}</div>`;
    } finally { if (button) button.disabled = false; }
  };
  PAGES.forecast = async (p) => {
    if (!S.book) return go('dashboard');
    const book = await api('GET', '/books/' + S.book);
    p.innerHTML = header('剧情推演', '模型基于当前作品事实生成可追溯的多分支因果路径', '<button class="btn btn-primary" id="forecast-run-btn" onclick="runForecast()">开始推演</button>') + `<div class="content"><div class="card"><div class="grid grid-3"><div class="field"><label>分支数量（1-8）</label><input class="input" id="fc-branches" type="number" value="3" min="1" max="8"></div><div class="field"><label>当前章节</label><input class="input" id="fc-chapter" type="number" value="${book.chaptersWritten || 0}" min="0"></div><div class="field"><label>推演深度（1-12）</label><input class="input" id="fc-depth" type="number" value="3" min="1" max="12"></div></div><div class="field"><label>额外上下文</label><textarea class="input textarea" id="fc-context" placeholder="目标、约束、想探索的冲突…"></textarea></div></div><div id="forecast-result"></div></div>`;
  };

  // ========== Continuous writing recovery ==========
  let enhancedContinuousTask = '';
  let stopContinuousPoll = null;
  function continuousPoll(taskId) {
    if (!taskId) return;
    if (stopContinuousPoll) stopContinuousPoll();
    stopContinuousPoll = pollEnhancedTask(taskId, document.getElementById('continuous-runtime'), '连续创作', async (task) => {
      if (task.status === 'completed') await reloadContinuousStatus();
      else if (task.status === 'needs_author_decision' || task.status === 'failed') await reloadContinuousStatus();
    });
  }
  function renderContinuousStatus(status) {
    const target = document.getElementById('continuous-runtime');
    if (!target) return;
    const total = Number(status?.totalRequested || 0);
    const completed = Number(status?.completed || 0);
    const checkpoint = status?.checkpoint || {};
    const checkpointValue = checkpoint.stage || checkpoint.message || checkpoint.lastAction || checkpoint.state || '';
    const checkpointText = checkpointValue && typeof checkpointValue === 'string' && typeof taskStageLabel === 'function' ? taskStageLabel(checkpointValue, status?.status) : checkpointValue;
    const decision = status?.decision || {};
    const reasonLabels = { child_chapter_not_accepted: '章节没有通过质量门禁', joint_review_not_passed: '联合审查没有通过', model_unavailable: '模型不可用' };
    const reason = reasonLabels[decision.reason] || decision.reason || status?.error || '';
    const decisionHtml = status?.status === 'needs_author_decision' ? `<div class="warn-banner mt8"><b>任务已安全停在这里</b><p class="mt8">${decision.chapter ? `第 ${esc(decision.chapter)} 章` : '当前章节'}${esc(reason ? `：${reason}` : '需要作者检查后再继续')}。</p>${decision.message ? `<p class="text-sm mt8">${esc(decision.message)}</p>` : ''}<div class="row row-wrap mt12"><button class="btn btn-sm btn-primary" onclick="continuousAuthorDecision('accept')">作者放行当前候选</button><button class="btn btn-sm btn-secondary" onclick="continuousAuthorDecision('reject')">重新审查</button><button class="btn btn-sm btn-danger" onclick="continuousAuthorDecision('cancel')">结束任务</button><button class="btn btn-sm btn-ghost" onclick="go('tasks')">查看任务详情</button></div></div>` : status?.status === 'failed' ? `<div class="warn-banner mt8">${esc(status.error || '任务失败')}<div class="row mt12"><button class="btn btn-sm btn-secondary" onclick="continuousTaskAction('retry')">重新尝试</button></div></div>` : '';
    target.innerHTML = `<div class="card"><div class="row"><b>持久任务状态</b><span class="spacer"></span>${statusBadge(status?.status || 'idle')}</div><div class="progress mt8"><div class="progress-bar" style="width:${total ? Math.min(100, completed / total * 100) : 0}%"></div></div><p class="dim-note mt8">已完成 ${completed}/${total || '—'} 章 · 当前章节 ${esc(status?.currentChapter ?? '—')} · Task ${esc(status?.taskId || '—')}</p>${checkpointText ? `<p class="text-sm text-muted mt8">当前阶段：${esc(typeof checkpointText === 'string' ? checkpointText : workspaceText(checkpointText))}</p>` : ''}${decisionHtml}</div>`;
  }
  async function reloadContinuousStatus() {
    try { renderContinuousStatus(await api('GET', `/books/${S.book}/continuous/status`)); } catch (error) { const target = document.getElementById('continuous-runtime'); if (target) target.innerHTML = `<div class="warn-banner">${esc(error.message)}</div>`; }
  }
  window.startEnhancedContinuous = async function () {
    const count = Number(document.getElementById('c-count')?.value || 0);
    const start = Number(document.getElementById('c-start')?.value || 0);
    const context = document.getElementById('c-context')?.value || '';
    if (!Number.isInteger(count) || count < 5 || count > 200) return toast('连续创作章数必须在 5 到 200 之间', 'error');
    if (!Number.isInteger(start) || start < 1) return toast('起始章节必须为正整数', 'error');
    if (!window.confirm(`确认启动 ${count} 章连续创作？任务会在后台持久执行。`)) return;
    try {
      const queued = await api('POST', `/books/${S.book}/continuous`, { count, startChapter: start, context });
      enhancedContinuousTask = queued.taskId;
      localStorage.setItem('novelforge-continuous-' + S.book, enhancedContinuousTask);
      toast('连续创作任务已排队', 'success');
      continuousPoll(enhancedContinuousTask);
    } catch (error) { if (!renderPlanningGate(document.getElementById('continuous-runtime'), error)) toast(error.message, 'error'); }
  };
  window.continuousTaskAction = async function (action) {
    const taskId = enhancedContinuousTask || localStorage.getItem('novelforge-continuous-' + S.book);
    if (!taskId) return toast('没有可操作的连续创作任务', 'warning');
    try { await api('POST', `/tasks/${taskId}/${action}`); enhancedContinuousTask = taskId; const latest = await api('GET', `/tasks/${taskId}`); await reloadContinuousStatus(); if (activeStatus.has(latest.status)) continuousPoll(taskId); toast('任务状态已更新', 'success'); }
    catch (error) { toast(error.message, 'error'); }
  };
  PAGES.continuous = async (p) => {
    if (!S.book) return go('dashboard');
    const book = await api('GET', '/books/' + S.book);
    const status = await api('GET', `/books/${S.book}/continuous/status`).catch(() => ({ status: 'idle' }));
    enhancedContinuousTask = status.taskId || localStorage.getItem('novelforge-continuous-' + S.book) || '';
    const active = activeStatus.has(status.status);
    const waitingForAuthor = status.status === 'needs_author_decision';
    const planningManaged = Boolean(book.creationWorkflow?.metadata?.requireCompletePlanning);
    const creationBlocked = planningManaged && !book.planningReadiness?.ready;
    const controls = status.status === 'running' ? `<button class="btn btn-sm btn-secondary" onclick="continuousTaskAction('pause')">暂停</button><button class="btn btn-sm btn-danger" onclick="continuousTaskAction('cancel')">取消</button>` : status.status === 'paused' ? `<button class="btn btn-sm btn-primary" onclick="continuousTaskAction('resume')">恢复</button><button class="btn btn-sm btn-danger" onclick="continuousTaskAction('cancel')">取消</button>` : status.status === 'queued' ? `<button class="btn btn-sm btn-danger" onclick="continuousTaskAction('cancel')">取消排队</button>` : waitingForAuthor ? `<button class="btn btn-sm btn-primary" onclick="continuousTaskAction('retry')">从检查点继续</button><button class="btn btn-sm btn-danger" onclick="continuousTaskAction('cancel')">结束任务</button>` : status.status === 'failed' ? `<button class="btn btn-sm btn-primary" onclick="continuousTaskAction('retry')">重新尝试</button>` : '';
    const startDisabled = active || waitingForAuthor || creationBlocked ? ' disabled' : '';
    p.innerHTML = header('连续创作', '配置来自持久项目设置；进度来自 TaskRuntime，而不是浏览器内存', `<button class="btn btn-secondary" onclick="go('tasks')">查看任务</button>`) + `<div class="content">${creationBlocked ? planningGateMarkup(book.planningReadiness, book.creationWorkflow?.mode) : ''}<div class="warn-banner">连续创作会逐章执行计划、写作、审查、修订和联合审查。刷新后仍可通过持久 taskId 恢复监控。</div><div class="grid grid-2"><div class="card"><h3>创作规划</h3><label class="fld">起始章节<input class="input" id="c-start" type="number" value="${(book.chaptersWritten || 0) + 1}" min="1"></label><label class="fld">连续创作篇目（5-200）<input class="input" id="c-count" type="number" value="20" min="5" max="200"></label><div class="grid grid-2"><label class="fld">门禁分数<input class="input" value="${book.passScore || 93}" disabled></label><label class="fld">联合审查间隔<input class="input" value="${book.jointReviewInterval || 5}" disabled></label></div><label class="fld">本轮创作指导<textarea class="input ta" id="c-context" placeholder="节奏、伏笔、人物弧线、要推进的冲突…"></textarea></label><button class="btn btn-primary" onclick="startEnhancedContinuous()"${startDisabled}>开始连续创作</button><div class="row row-wrap mt16">${controls}</div></div><div id="continuous-runtime"></div></div></div>`;
    renderContinuousStatus(status);
    if (enhancedContinuousTask && active) continuousPoll(enhancedContinuousTask);
  };

  // ========== Style manager ==========
  window.analyzeStyle = async function () {
    const text = document.getElementById('style-sample')?.value || '';
    const output = document.getElementById('style-result');
    try { const profile = await api('POST', '/style/analyze', { text, sourceName: document.getElementById('style-source')?.value || 'sample' }); if (output) output.innerHTML = `<div class="card"><h3>分析结果</h3><div class="grid grid-3">${[['字符数', profile.charCount], ['句子数', profile.sentenceCount], ['平均句长', profile.avgSentenceLength], ['句长标准差', profile.sentenceLengthStdDev], ['平均段落', profile.avgParagraphLength], ['词汇多样性', profile.vocabularyDiversity]].map(([label, value]) => `<div class="kv"><span>${label}</span><b>${esc(value)}</b></div>`).join('')}</div><p class="dim-note mt16">特征：${esc([...(profile.topPatterns || []), ...(profile.rhetoricalFeatures || [])].join('、') || '未发现明显特征')}</p><div class="card mt16"><h4>整理后的分析</h4>${readableProjection(profile)}</div></div>`; window.__lastStyleProfile = profile; }
    catch (error) { if (output) output.innerHTML = `<div class="warn-banner">${esc(error.message)}</div>`; }
  };
  window.importStyle = async function () {
    if (!S.book) return toast('请先打开一本作品', 'warning');
    const text = document.getElementById('style-sample')?.value || '';
    try { await api('POST', `/books/${S.book}/style/import`, { text, sourceName: document.getElementById('style-source')?.value || 'sample' }); toast('文风指南已写入当前作品', 'success'); }
    catch (error) { toast(error.message, 'error'); }
  };
  PAGES.style = async (p) => {
    await ensureBookList();
    p.innerHTML = header('文风管理', '分析可复用的语言特征，并把结果持久化到作品写作风格', `<button class="btn btn-primary" onclick="analyzeStyle()">分析样本</button>`) + `<div class="content"><div class="grid grid-2"><div class="card"><label class="fld">样本名称<input class="input" id="style-source" value="sample"></label><label class="fld">参考文本<textarea class="input textarea" id="style-sample" style="min-height:340px" placeholder="粘贴需要分析的正文…"></textarea></label><button class="btn btn-secondary" onclick="importStyle()" ${S.book ? '' : 'disabled'}>导入到当前作品</button></div><div id="style-result"><div class="card"><p class="dim-note">样本分析会返回句长、段落、词汇多样性和检测到的修辞特征。</p></div></div></div></div>`;
  };

  // ========== Radar ==========
  function radarHistoryHtml(data) { return (data.history || []).map((item) => `<details class="card"><summary><b>${esc(item.generatedAt || item.file)}</b> · ${item.recommendationCount || 0} 条建议</summary><p class="dim-note mt8">${esc(item.marketSummary || '模型未提供摘要')}</p><div class="mt8">${readableProjection(item.result)}</div></details>`).join('') || '<div class="card"><p class="dim-note">还没有已完成的扫描。</p></div>'; }
  window.runRadar = async function () {
    const output = document.getElementById('radar-runtime');
    try { const queued = await api('POST', '/radar/scan'); const task = await waitForTask(queued.taskId, (current) => renderTaskState(output, current, '题材雷达')); if (task.status !== 'completed') throw new Error(taskError(task)); if (output) output.innerHTML = `<div class="card"><h3>本次扫描结果</h3>${readableProjection(task.result)}</div>`; const history = await api('GET', '/radar/history'); const list = document.getElementById('radar-history'); if (list) list.innerHTML = radarHistoryHtml(history); toast('雷达扫描已完成并写入历史', 'success'); }
    catch (error) { if (output) output.innerHTML = `<div class="warn-banner">扫描失败：${esc(error.message)}</div>`; }
  };
  PAGES.radar = async (p) => {
    const history = await api('GET', '/radar/history');
    p.innerHTML = header('题材雷达', '基于本地作品与题材规则的模型研究；不伪装成实时平台数据', `<button class="btn btn-primary" onclick="runRadar()">开始扫描</button>`) + `<div class="content"><div id="radar-runtime"></div><h3 class="mb8">扫描历史</h3><div id="radar-history">${radarHistoryHtml(history)}</div></div>`;
  };

  // ========== InkOS creative mode launcher ==========
  const studioModes = [
    ['short', '短篇小说', '单一冲突、有限角色和完整结尾的短篇创作'],
    ['script', '剧本', '场景、对白、动作和镜头说明'],
    ['storyboard', '分镜', '按镜头拆解画面、节奏和转场'],
    ['interactive-film', '互动影像', '节点、选项、状态变化和结局设计'],
    ['play-guided', '分支互动', '每轮选择一个有因果差异的行动'],
    ['play-open', '开放互动', '依据已确认作品事实回应自由行动'],
    ['fanfic', '同人创作', '保留原作边界并记录新增设定'],
    ['spinoff', '衍生创作', '从当前作品事实派生独立主线'],
    ['imitation', '风格研究', '提炼技法而不复制原文'],
    ['cover-brief', '封面策划', '生成可交付给设计师/图像模型的封面简报'],
  ];
  window.launchStudioMode = async function (mode) {
    chatMode = mode;
    localStorage.setItem('novelforge-chat-mode', mode);
    go('chat');
  };
  PAGES.modes = async (p) => {
    p.innerHTML = header('创作模式', 'InkOS 的短篇、剧本、互动和衍生创作入口统一接入持久化 Studio Chat；每种模式都会把约束写入真实模型请求', '') + `<div class="content"><div class="warn-banner">封面策划输出的是可执行的设计简报；当前项目没有图像 Provider，因此不会伪造已生成的图片。</div><div class="grid grid-3">${studioModes.map(([id, title, description]) => `<div class="card card-hover"><h3>${esc(title)}</h3><p class="dim-note" style="min-height:42px">${esc(description)}</p><button class="btn btn-primary mt16" onclick="launchStudioMode('${escAttr(id)}')">进入${esc(title)}</button></div>`).join('')}</div><div class="card mt16"><h3>当前作品</h3><p class="dim-note">${S.book ? `模式会自动带入「${esc(bookName())}」的世界观、角色、意图和伏笔上下文。` : '未选择作品；通用模式仍可使用，选择作品后可获得上下文感知。'}</p></div></div>`;
  };

  // ========== Translation workspace ==========
  let translationUpload = null;
  let selectedTranslation = '';
  async function loadTranslationDetail(id) {
    selectedTranslation = id || '';
    if (!id) return null;
    return api('GET', `/translations/${encodeURIComponent(id)}`);
  }
  window.uploadTranslation = async function () {
    const input = document.getElementById('translation-file');
    const file = input?.files?.[0];
    if (!file) return toast('请先选择文本文件', 'warning');
    try {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(reader.error || new Error('读取文件失败'));
        reader.onload = () => resolve(String(reader.result || ''));
        reader.readAsDataURL(file);
      });
      translationUpload = await api('POST', '/translations/upload', { filename: file.name, dataUrl });
      document.getElementById('translation-upload-status').textContent = `已上传 ${translationUpload.filename}（${translationUpload.size} bytes）`;
    } catch (error) { toast(error.message, 'error'); }
  };
  window.createTranslation = async function () {
    if (!translationUpload) return toast('请先上传源文件', 'warning');
    try {
      const created = await api('POST', '/translations/create', { filePath: translationUpload.storedPath, title: document.getElementById('translation-title')?.value || '', sourceLanguage: document.getElementById('translation-source')?.value || 'auto', targetLanguage: document.getElementById('translation-target')?.value || 'zh', segmentMaxChars: Number(document.getElementById('translation-segment')?.value || 1200) });
      selectedTranslation = created.projectId;
      translationUpload = null;
      toast('翻译项目已创建', 'success');
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };
  window.runTranslation = async function (id) {
    const target = document.getElementById('translation-runtime');
    try {
      const queued = await api('POST', `/translations/${encodeURIComponent(id)}/run`, { batchSize: Number(document.getElementById('translation-batch')?.value || 8) });
      const task = await waitForTask(queued.taskId, (current) => renderTaskState(target, current, '翻译任务'));
      if (task.status !== 'completed') throw new Error(taskError(task));
      toast('翻译任务已完成，结果已持久化', 'success');
      await render();
    } catch (error) { if (target) target.innerHTML = `<div class="warn-banner">翻译失败：${esc(error.message)}</div>`; }
  };
  window.exportTranslation = async function (id, format) {
    try {
      const response = await fetch(`/api/v1/translations/${encodeURIComponent(id)}/export?format=${encodeURIComponent(format)}`, { method: 'POST' });
      if (!response.ok) { const detail = await response.json().catch(() => ({ detail: response.statusText })); throw new Error(detail.detail || response.statusText); }
      const href = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a'); anchor.href = href; anchor.download = response.headers.get('content-disposition')?.match(/filename="?([^";]+)"?/i)?.[1] || `${id}.${format}`; document.body.appendChild(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(href);
    } catch (error) { toast(error.message, 'error'); }
  };
  PAGES.translation = async (p) => {
    const data = await api('GET', '/translations');
    const projects = data.translations || [];
    if (!selectedTranslation || !projects.some((item) => item.projectId === selectedTranslation)) selectedTranslation = projects[0]?.projectId || '';
    const detail = selectedTranslation ? await loadTranslationDetail(selectedTranslation) : null;
    const manifest = detail?.manifest;
    p.innerHTML = header('翻译工作台', '上传源文档，分段调用真实模型翻译；任务失败时保留已完成段落，支持刷新后继续', '<button class="btn btn-secondary" onclick="go(\'translation\')">刷新</button>') + `<div class="content"><div class="grid grid-2"><div class="card"><h3>新建翻译项目</h3><label class="fld">源文件<input class="input" id="translation-file" type="file" accept="text/*"></label><div id="translation-upload-status" class="dim-note">尚未上传</div><button class="btn btn-secondary" onclick="uploadTranslation()">上传源文件</button><div class="grid grid-2 mt16"><label class="fld">源语言<input class="input" id="translation-source" value="auto"></label><label class="fld">目标语言<input class="input" id="translation-target" value="zh"></label></div><label class="fld">项目标题<input class="input" id="translation-title" placeholder="可选"></label><label class="fld">分段最大字符数<input class="input" id="translation-segment" type="number" min="400" max="4000" value="1200"></label><button class="btn btn-primary" onclick="createTranslation()">创建翻译项目</button></div><div><div class="card"><div class="card-title-row"><h3>已有项目</h3><span class="badge badge-muted">${projects.length}</span></div>${projects.map((item) => `<button class="btn btn-ghost" style="width:100%;justify-content:flex-start;text-align:left;margin-bottom:4px;${item.projectId === selectedTranslation ? 'background:var(--primary-dim);color:var(--primary)' : ''}" onclick="selectedTranslation='${escAttr(item.projectId)}';render()"><span>${esc(item.title)}</span><span class="spacer"></span><span class="text-sm text-muted">${esc(item.sourceLanguage)} → ${esc(item.targetLanguage)}</span></button>`).join('') || '<p class="dim-note">暂无翻译项目。</p>'}</div>${manifest ? `<div class="card mt16"><div class="card-title-row"><h3>${esc(manifest.title)}</h3><div class="row"><label class="text-sm">批量<input class="input" id="translation-batch" style="width:70px;display:inline-block;margin-left:5px" type="number" min="1" max="32" value="8"></label><button class="btn btn-primary" onclick="runTranslation('${escAttr(selectedTranslation)}')">开始翻译</button></div></div><p class="dim-note">${esc(manifest.sourceLanguage)} → ${esc(manifest.targetLanguage)} · ${manifest.chapters?.length || 0} 章 · ${esc(detail.report || '尚未运行')}</p><div class="row row-wrap mt8"><button class="btn btn-sm btn-secondary" onclick="exportTranslation('${escAttr(selectedTranslation)}','md')">纯文本</button><button class="btn btn-sm btn-secondary" onclick="exportTranslation('${escAttr(selectedTranslation)}','txt')">纯文本（通用）</button><button class="btn btn-sm btn-secondary" onclick="exportTranslation('${escAttr(selectedTranslation)}','epub')">EPUB</button></div><div id="translation-runtime"></div><div class="mt16">${(detail.chapters || []).map((chapter) => `<details class="card"><summary>${esc(chapter.number)}. ${esc(chapter.title)} · ${esc(chapter.status)}</summary>${(chapter.segments || []).map((segment) => `<div class="list-row"><span class="badge badge-muted">${esc(segment.index)}</span><div style="flex:1"><div>${esc(segment.source)}</div><div class="dim-note mt8">${esc(segment.target || '待翻译')}</div></div></div>`).join('')}</details>`).join('')}</div></div>` : '<div class="card mt16"><p class="dim-note">选择一个翻译项目查看段落和导出操作。</p></div>'}</div></div></div>`;
  };

  // ========== Prompt registry ==========
  window.fillPrompt = function (index) {
    const prompt = window.__promptRows?.[index];
    if (!prompt) return;
    document.getElementById('prompt-task').value = prompt.task_type || '';
    document.getElementById('prompt-system').value = prompt.system_prompt || '';
    document.getElementById('prompt-template').value = prompt.user_template || '';
    document.getElementById('prompt-description').value = prompt.description || '';
  };
  window.saveStudioPrompt = async function () {
    const taskType = document.getElementById('prompt-task')?.value.trim();
    if (!taskType) return toast('task type 不能为空', 'error');
    try { await api('POST', '/prompts' + (S.book ? `?project_id=${encodeURIComponent(S.book)}` : ''), { task_type: taskType, system_prompt: document.getElementById('prompt-system').value, user_template: document.getElementById('prompt-template').value, description: document.getElementById('prompt-description').value }); toast('提示词版本已保存', 'success'); await render(); }
    catch (error) { toast(error.message, 'error'); }
  };
  window.restoreStudioPrompts = async function () { try { await api('POST', '/prompts/restore-defaults' + (S.book ? `?project_id=${encodeURIComponent(S.book)}` : '')); toast('默认提示词已恢复', 'success'); await render(); } catch (error) { toast(error.message, 'error'); } };
  window.deleteStudioPrompt = async function (id) { if (!window.confirm('删除这个提示词版本？')) return; try { await api('DELETE', '/prompts/' + encodeURIComponent(id)); await render(); } catch (error) { toast(error.message, 'error'); } };
  PAGES.prompts = async (p) => {
    const [data, types] = await Promise.all([api('GET', '/prompts' + (S.book ? `?project_id=${encodeURIComponent(S.book)}` : '')), api('GET', '/prompts/task-types')]);
    window.__promptRows = data.prompts || [];
    const taskTypes = types.task_types || [];
    p.innerHTML = header('提示词注册表', '版本化管理写作、审查、Story Bible 和研究任务的提示词', `<button class="btn btn-secondary" onclick="restoreStudioPrompts()">恢复默认</button>`) + `<div class="content"><div class="grid grid-2"><div class="card"><h3>编辑版本</h3><label class="fld">任务类型<select class="input" id="prompt-task">${taskTypes.map((item) => `<option value="${escAttr(item)}">${esc(item)}</option>`).join('')}</select></label><label class="fld">System prompt<textarea class="input ta" id="prompt-system"></textarea></label><label class="fld">User template<textarea class="input ta" id="prompt-template"></textarea></label><label class="fld">说明<input class="input" id="prompt-description"></label><button class="btn btn-primary" onclick="saveStudioPrompt()">保存新版本</button></div><div><div class="card"><h3>已持久化版本</h3>${window.__promptRows.map((row, index) => `<div class="list-row"><div><b>${esc(row.task_type)}</b><div class="dim-note">v${esc(row.version)} · ${row.is_default ? '默认' : '项目覆盖'}</div></div><span class="spacer"></span><button class="btn btn-sm btn-secondary" onclick="fillPrompt(${index})">编辑</button>${row.id ? `<button class="btn btn-sm btn-danger" onclick="deleteStudioPrompt('${escAttr(row.id)}')">删除</button>` : ''}</div>`).join('') || '<p class="dim-note">还没有自定义版本，将使用内置默认。</p>'}</div></div></div></div>`;
    if (window.__promptRows[0]) fillPrompt(0);
  };

  // ========== Backups / daemon / logs ==========
  window.createStudioBackup = async function () { try { await api('POST', '/backup', { project_id: S.book, description: document.getElementById('backup-description')?.value || 'Studio 手动备份' }); toast('备份已创建', 'success'); await render(); } catch (error) { toast(error.message, 'error'); } };
  window.restoreStudioBackup = async function (id) { if (!window.confirm('恢复会覆盖当前 SQLite 数据，并先创建恢复前备份。继续？')) return; try { await api('POST', '/backups/' + encodeURIComponent(id) + '/restore'); toast('恢复完成，请刷新 Studio', 'success'); await render(); } catch (error) { toast(error.message, 'error'); } };
  window.deleteStudioBackup = async function (id) { if (!window.confirm('删除这个备份文件？')) return; try { await api('DELETE', '/backups/' + encodeURIComponent(id)); await render(); } catch (error) { toast(error.message, 'error'); } };
  PAGES.backups = async (p) => {
    const query = S.book ? `?project_id=${encodeURIComponent(S.book)}` : '';
    const [data, stats] = await Promise.all([api('GET', '/backups' + query), api('GET', '/backups/statistics' + query)]);
    p.innerHTML = header('备份与恢复', '查看完整性、创建手动快照并在恢复前自动保护当前数据库', '<button class="btn btn-primary" onclick="createStudioBackup()">创建备份</button>') + `<div class="content"><div class="card"><div class="grid grid-3"><div class="kv"><span>备份数</span><b>${esc(stats.total_count ?? data.count ?? 0)}</b></div><div class="kv"><span>总大小</span><b>${esc(stats.total_size_bytes ?? 0)} bytes</b></div><label class="fld">描述<input class="input" id="backup-description" value="Studio 手动备份"></label></div></div><div class="card"><h3>备份历史</h3>${(data.backups || []).map((item) => `<div class="list-row"><div><b>${esc(item.backup_type)}</b> · ${esc(item.created_at)}</div><span class="spacer"></span><span class="text-sm ${item.exists ? 'text-muted' : 'badge-error'}">${item.exists ? `${item.size_bytes} bytes` : '文件缺失'}</span><button class="btn btn-sm btn-secondary" ${item.exists ? '' : 'disabled'} onclick="restoreStudioBackup('${escAttr(item.id)}')">恢复</button><button class="btn btn-sm btn-danger" onclick="deleteStudioBackup('${escAttr(item.id)}')">删除</button></div>`).join('') || '<p class="dim-note">暂无备份。</p>'}</div></div>`;
  };
  async function renderLogs(target) { const data = await api('GET', '/logs?limit=200'); if (target) target.innerHTML = (data.entries || []).map((entry) => `<div class="log-line" style="border-left-color:${entry.level === 'error' ? 'var(--error)' : 'var(--border)'}"><span class="text-muted">${esc(entry.timestamp || '')}</span> <b>${esc(entry.tag || '')}</b> ${esc(entry.message || '')}</div>`).join('') || '<p class="dim-note">暂无运行日志。</p>'; }
  PAGES.logs = async (p) => { p.innerHTML = header('运行日志', '读取持久化 operation_logs 和任务失败记录', '<button class="btn btn-secondary" onclick="go(\'logs\')">刷新</button>') + '<div class="content"><div class="card"><div id="logs-box" class="log-box" style="height:65vh"></div></div></div>'; await renderLogs(document.getElementById('logs-box')); };
  window.refreshDaemon = async function () { const data = await api('GET', '/daemon'); const output = document.getElementById('daemon-status'); if (output) output.innerHTML = `<div class="card"><div class="row"><b>Worker</b><span class="spacer"></span>${data.running ? '<span class="badge badge-success">运行中</span>' : '<span class="badge badge-muted">已停止</span>'}</div><p class="dim-note mt8">${esc(data.workerId || '无活动 worker')} · 环境禁用标志：${data.disabledByEnvironment ? '是' : '否'}</p></div>`; return data; };
  window.startStudioDaemon = async function () { try { await api('POST', '/daemon/start'); await refreshDaemon(); toast('Worker 已启动', 'success'); } catch (error) { toast(error.message, 'error'); } };
  window.stopStudioDaemon = async function () { try { await api('POST', '/daemon/stop'); await refreshDaemon(); toast('Worker 已停止', 'success'); } catch (error) { toast(error.message, 'error'); } };
  PAGES.daemon = async (p) => { p.innerHTML = header('守护进程', '控制 durable TaskRuntime 的 Studio worker', `<button class="btn btn-primary" onclick="startStudioDaemon()">启动</button><button class="btn btn-secondary" onclick="stopStudioDaemon()">停止</button>`) + '<div class="content"><div id="daemon-status"></div><div class="card"><h3>最近日志</h3><div id="daemon-logs" class="log-box"></div></div></div>'; await refreshDaemon(); await renderLogs(document.getElementById('daemon-logs')); };

  // ========== Character themes ==========
  window.createStudioTheme = async function () { try { await api('POST', `/books/${S.book}/themes`, { name: document.getElementById('theme-name').value, characterId: document.getElementById('theme-character').value || null, primaryColor: document.getElementById('theme-primary').value, secondaryColor: document.getElementById('theme-secondary').value, accentColor: document.getElementById('theme-accent').value }); toast('主题已保存', 'success'); await render(); } catch (error) { toast(error.message, 'error'); } };
  window.deleteStudioTheme = async function (id) { if (!window.confirm('删除这个人物主题？')) return; try { await api('DELETE', `/books/${S.book}/themes/${encodeURIComponent(id)}`); await render(); } catch (error) { toast(error.message, 'error'); } };
  PAGES.themes = async (p) => { const [book, data] = await Promise.all([api('GET', `/books/${S.book}`), api('GET', `/books/${S.book}/themes`)]); const characters = Object.entries(book.characters || {}); p.innerHTML = header('人物主题', '为人物配置可持久化的色彩和排版主题', '<button class="btn btn-primary" onclick="createStudioTheme()">保存主题</button>') + `<div class="content"><div class="card"><div class="grid grid-2"><label class="fld">名称<input class="input" id="theme-name" placeholder="主角主题"></label><label class="fld">人物<select class="input" id="theme-character"><option value="">全局</option>${characters.map(([name]) => `<option value="${escAttr(name)}">${esc(name)}</option>`).join('')}</select></label></div><div class="grid grid-3"><label class="fld">主色<input class="input" id="theme-primary" type="color" value="#e94560"></label><label class="fld">辅色<input class="input" id="theme-secondary" type="color" value="#0f3460"></label><label class="fld">强调色<input class="input" id="theme-accent" type="color" value="#16213e"></label></div></div><div class="card"><h3>已保存主题</h3>${(data.themes || []).map((theme) => `<div class="list-row"><span style="width:14px;height:14px;border-radius:50%;background:${escAttr(theme.primary_color)}"></span><b>${esc(theme.name)}</b><span class="text-sm text-muted">${esc(theme.character_id || '全局')}</span><span class="spacer"></span><button class="btn btn-sm btn-danger" onclick="deleteStudioTheme('${escAttr(theme.id)}')">删除</button></div>`).join('') || '<p class="dim-note">暂无主题。</p>'}</div></div>`; };

  // Keep the existing document-ingestion page and append the missing InkOS
  // derivative/canon import workflows to the same surface.
  let studioImportMode = 'fanfic';
  const originalImportPage = PAGES.import;
  window.setStudioImportMode = function (mode) { studioImportMode = mode; renderStudioImportMode(); };
  window.runStudioImport = async function () {
    try {
      let result;
      if (studioImportMode === 'canon') {
        result = await api('POST', `/books/${encodeURIComponent(document.getElementById('import-canon-target')?.value || '')}/import/canon`, { fromBookId: document.getElementById('import-canon-source')?.value || '' });
      } else if (studioImportMode === 'fanfic') {
        result = await api('POST', '/fanfic/init', { title: document.getElementById('import-fanfic-title')?.value || '', sourceText: document.getElementById('import-fanfic-text')?.value || '', mode: document.getElementById('import-fanfic-mode')?.value || 'canon', genre: document.getElementById('import-fanfic-genre')?.value || 'other', language: document.getElementById('import-fanfic-language')?.value || 'zh' });
      } else if (studioImportMode === 'spinoff') {
        result = await api('POST', '/spinoff/init', { title: document.getElementById('import-spinoff-title')?.value || '', parentBookId: document.getElementById('import-spinoff-parent')?.value || '', direction: document.getElementById('import-spinoff-direction')?.value || '' });
      } else if (studioImportMode === 'imitation') {
        result = await api('POST', '/imitation/init', { title: document.getElementById('import-imitation-title')?.value || '', referenceText: document.getElementById('import-imitation-reference')?.value || '', storyIdea: document.getElementById('import-imitation-idea')?.value || '', genre: document.getElementById('import-imitation-genre')?.value || 'other', language: document.getElementById('import-imitation-language')?.value || 'zh' });
      }
      toast('导入工作流已提交', 'success');
      if (result?.bookId) await openBook(result.bookId);
      else await render();
    } catch (error) { toast(error.message, 'error'); }
  };
  function renderStudioImportMode() {
    const target = document.getElementById('studio-import-mode-form');
    if (!target) return;
    const options = S.books.map((book) => `<option value="${escAttr(book.id)}">${esc(book.title)}</option>`).join('');
    if (studioImportMode === 'canon') target.innerHTML = `<div class="grid grid-2"><label class="fld">源作品<select class="input" id="import-canon-source">${options}</select></label><label class="fld">目标作品<select class="input" id="import-canon-target">${options}</select></label></div><p class="dim-note">复制世界观、人物、势力、地点和伏笔到目标作品；不会覆盖章节正文。</p>`;
    if (studioImportMode === 'fanfic') target.innerHTML = `<div class="grid grid-3"><label class="fld">标题<input class="input" id="import-fanfic-title"></label><label class="fld">模式<select class="input" id="import-fanfic-mode"><option value="canon">遵循原作</option><option value="au">架空 AU</option><option value="ooc">OOC 研究</option><option value="cp">配对 CP</option></select></label><label class="fld">语言<input class="input" id="import-fanfic-language" value="zh"></label></div><label class="fld">题材<input class="input" id="import-fanfic-genre" value="other"></label><label class="fld">原作资料<textarea class="input textarea" id="import-fanfic-text" style="min-height:180px"></textarea></label>`;
    if (studioImportMode === 'spinoff') target.innerHTML = `<label class="fld">标题<input class="input" id="import-spinoff-title"></label><label class="fld">父作品<select class="input" id="import-spinoff-parent">${options}</select></label><label class="fld">衍生方向<textarea class="input textarea" id="import-spinoff-direction" style="min-height:140px" placeholder="主角、时代、冲突或想保留的设定"></textarea></label>`;
    if (studioImportMode === 'imitation') target.innerHTML = `<div class="grid grid-3"><label class="fld">标题<input class="input" id="import-imitation-title"></label><label class="fld">题材<input class="input" id="import-imitation-genre" value="other"></label><label class="fld">语言<input class="input" id="import-imitation-language" value="zh"></label></div><label class="fld">故事想法<textarea class="input textarea" id="import-imitation-idea" style="min-height:110px"></textarea></label><label class="fld">参考文本<textarea class="input textarea" id="import-imitation-reference" style="min-height:180px"></textarea></label>`;
  }
  PAGES.import = async (p) => {
    await originalImportPage(p);
    await ensureBookList();
    const host = p.querySelector('.content') || p;
    const wrapper = document.createElement('div');
    wrapper.className = 'card mt16';
    wrapper.innerHTML = `<div class="card-title-row"><div><h3>InkOS 衍生导入</h3><p class="dim-note mt8">同人、衍生、风格研究和 Canon 复制均写入真实作品数据或 durable 世界观任务。</p></div><button class="btn btn-primary" onclick="runStudioImport()">提交工作流</button></div><div class="tabs"><button class="btn btn-sm ${studioImportMode === 'fanfic' ? 'btn-primary' : 'btn-ghost'}" onclick="setStudioImportMode('fanfic')">同人</button><button class="btn btn-sm ${studioImportMode === 'spinoff' ? 'btn-primary' : 'btn-ghost'}" onclick="setStudioImportMode('spinoff')">衍生</button><button class="btn btn-sm ${studioImportMode === 'imitation' ? 'btn-primary' : 'btn-ghost'}" onclick="setStudioImportMode('imitation')">风格研究</button><button class="btn btn-sm ${studioImportMode === 'canon' ? 'btn-primary' : 'btn-ghost'}" onclick="setStudioImportMode('canon')">复制 Canon</button></div><div id="studio-import-mode-form"></div>`;
    host.appendChild(wrapper);
    renderStudioImportMode();
  };

  // ========== DaVinci-style plot workspace ==========
  // The canvas is a durable authoring projection: moving or adding a draft
  // branch never mutates chapter truth until the author explicitly writes it.
  const plotKindLabels = { book: '作品', chapter: '章节', event: '时间事件', character: '人物', faction: '势力', location: '地点', foreshadow: '伏笔', forecast: 'AI 分支', 'forecast-step': '推演节点', note: '自定义节点' };
  let plotState = { graph: null, revision: 0, view: 'timeline', selectedId: '', showHidden: false };
  window.__plotForecastBranches = [];
  function plotNodeVisible(node) {
    if (node.hidden && !plotState.showHidden) return false;
    if (plotState.view === 'all') return true;
    if (plotState.view === 'timeline') return ['book', 'chapter', 'event', 'forecast', 'forecast-step', 'note'].includes(node.kind || node.type);
    return ['book', 'character', 'faction', 'location', 'foreshadow', 'forecast', 'forecast-step', 'note'].includes(node.kind || node.type);
  }
  function plotNodeColor(node) {
    const kind = node.kind || node.type;
    return kind === 'forecast' || kind === 'forecast-step' ? '#c084fc' : kind === 'chapter' || kind === 'event' ? '#60a5fa' : kind === 'character' ? '#34d399' : kind === 'location' ? '#fbbf24' : '#93a4bb';
  }
  function plotNodeTitle(node) { return String(node.title || node.label || node.id || '').slice(0, 24); }
  function readableMetadata(metadata) {
    return Object.entries(metadata || {}).filter(([key, value]) => !['raw', 'content', 'sourceDocuments', 'sections'].includes(key) && value !== null && value !== undefined && value !== '').slice(0, 12).map(([key, value]) => `<div class="kv"><span>${esc(key)}</span><b style="white-space:pre-wrap;text-align:right;max-width:70%">${esc(workspaceText(value) || '—')}</b></div>`).join('') || '<p class="dim-note">暂无补充字段。</p>';
  }
  function plotScenePoint(event, scene) {
    const point = document.getElementById('plot-svg').createSVGPoint(); point.x = event.clientX; point.y = event.clientY;
    return point.matrixTransform(scene.getScreenCTM().inverse());
  }
  function renderPlotInspector() {
    const detail = document.getElementById('plot-detail'); if (!detail) return;
    const node = (plotState.graph?.nodes || []).find(item => item.id === plotState.selectedId);
    if (!node) { detail.innerHTML = '<p class="dim-note">点击节点查看详情；拖拽节点可调整剧情布局。</p>'; return; }
    const kind = node.kind || node.type || 'note';
    detail.innerHTML = `<div class="card-title-row"><div><span class="badge badge-info">${esc(plotKindLabels[kind] || kind)}</span><h3 class="mt8">${esc(plotNodeTitle(node))}</h3></div><span class="badge ${node.source === 'ai' ? 'badge-warning' : node.hidden ? 'badge-error' : 'badge-muted'}">${esc(node.hidden ? '已隐藏' : node.source === 'ai' ? '草稿分支' : '事实投影')}</span></div>
      <label class="fld">标题<input class="input" id="plot-node-title" value="${escAttr(node.title || node.label || '')}"></label>
      <label class="fld">摘要<textarea class="input textarea" id="plot-node-summary" style="min-height:80px">${esc(node.summary || '')}</textarea></label>
      <label class="fld">细节<textarea class="input textarea" id="plot-node-description" style="min-height:120px">${esc(node.description || '')}</textarea></label>
      <div class="row row-wrap"><button class="btn btn-primary" onclick="savePlotNode()">保存节点</button><button class="btn btn-secondary" onclick="togglePlotNodeHidden()">${node.hidden ? '恢复显示' : '隐藏节点'}</button><button class="btn btn-danger" onclick="removePlotNode()">删除 / 隐藏</button></div>
      <details class="mt16"><summary>来源字段</summary><div class="mt8">${readableMetadata(node.metadata)}</div></details>`;
  }
  async function reloadPlotCanvas() {
    const data = await api('GET', `/books/${S.book}/plot-canvas`);
    plotState.graph = data.graph || { nodes: [], edges: [] }; plotState.revision = data.revision || 1;
    if (!plotState.selectedId || !plotState.graph.nodes.some(node => node.id === plotState.selectedId)) plotState.selectedId = plotState.graph.nodes[0]?.id || '';
  }
  async function savePlotDelta(delta) {
    try {
      const preview = await api('POST', `/books/${S.book}/story-graph/planning/preview`, {
        delta,
        expectedRevision: plotState.revision,
      });
      const diff = preview.previewDiff || {};
      const nodeCounts = diff.nodes?.counts || {};
      const edgeCounts = diff.edges?.counts || {};
      if (!diff.hasChanges) {
        toast('这次规划变更没有产生差异，未创建新的 revision。', '');
        return;
      }
      const approved = window.confirm(
        `确认写入 StoryFlow planning overlay？\n` +
        `节点：新增 ${nodeCounts.added || 0}、修改 ${nodeCounts.changed || 0}、移除 ${nodeCounts.removed || 0}\n` +
        `关系：新增 ${edgeCounts.added || 0}、修改 ${edgeCounts.changed || 0}、移除 ${edgeCounts.removed || 0}\n` +
        `这不会写入 Canon。`
      );
      if (!approved) {
        toast('已取消，规划 overlay 未改变。', '');
        return;
      }
      const data = await api('POST', `/books/${S.book}/plot-canvas/delta`, {
        delta,
        proposalId: preview.proposal?.proposalId,
        expectedRevision: plotState.revision,
      });
      plotState.graph = data.graph; plotState.revision = data.revision; drawPlotGraph(); renderPlotInspector();
    } catch (error) {
      await reloadPlotCanvas().catch(() => {}); drawPlotGraph(); renderPlotInspector();
      toast(error.message.includes('PLOT_REVISION_CONFLICT') ? '画布已被其他标签页更新，已重新载入最新版本。' : error.message, 'error');
    }
  }
  window.savePlotNode = async function () {
    const node = (plotState.graph?.nodes || []).find(item => item.id === plotState.selectedId); if (!node) return;
    await savePlotDelta({ operations: [{ op: 'update_node', id: node.id, patch: { title: document.getElementById('plot-node-title')?.value || '', label: document.getElementById('plot-node-title')?.value || '', summary: document.getElementById('plot-node-summary')?.value || '', description: document.getElementById('plot-node-description')?.value || '' } }] });
  };
  window.removePlotNode = async function () { if (plotState.selectedId) await savePlotDelta({ operations: [{ op: 'remove_node', id: plotState.selectedId }] }); };
  window.togglePlotNodeHidden = async function () { const node=(plotState.graph?.nodes||[]).find(item=>item.id===plotState.selectedId); if(node) await savePlotDelta({operations:[{op:node.hidden?'show_node':'hide_node',id:node.id}]}); };
  window.togglePlotHidden = function () { plotState.showHidden=!plotState.showHidden; drawPlotGraph(); renderPlotInspector(); };
  window.addPlotNote = async function () {
    const title=window.prompt('新节点名称','新的剧情想法'); if(!title?.trim()) return;
    const source=(plotState.graph?.nodes||[]).find(item=>item.id===plotState.selectedId); const baseX=Number(source?.x||520), baseY=Number(source?.y||260);
    await savePlotDelta({operations:[{op:'add_node',node:{id:'author:'+Date.now(),kind:'note',type:'note',label:title.trim(),title:title.trim(),summary:'',description:'',x:baseX+220,y:baseY+110,source:'author',customized:true,metadata:{createdFrom:'interactive-canvas'}}}]});
  };
  window.setPlotView = function (view) { plotState.view = view; drawPlotGraph(); renderPlotInspector(); };
  function drawPlotGraph() {
    const svg = document.getElementById('plot-svg'); if (!svg || !plotState.graph) return;
    const allNodes = plotState.graph.nodes || []; const nodes = allNodes.filter(plotNodeVisible); const visibleIds = new Set(nodes.map(node => node.id));
    const width = Math.max(1180, ...nodes.map(node => Number(node.x || 0) + 180), 1180); const height = Math.max(680, ...nodes.map(node => Number(node.y || 0) + 100), 680);
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    const edgeMarkup = (plotState.graph.edges || []).filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target)).map(edge => { const a = allNodes.find(node => node.id === edge.source), b = allNodes.find(node => node.id === edge.target); if (!a || !b) return ''; const ax = Number(a.x || 0), ay = Number(a.y || 0), bx = Number(b.x || 0), by = Number(b.y || 0), dx = (bx - ax) * .42; const path = `M ${ax} ${ay} C ${ax + dx} ${ay}, ${bx - dx} ${by}, ${bx} ${by}`; return `<path d="${path}" fill="none" stroke="${edge.kind === 'forecast' ? '#c084fc' : 'var(--border)'}" stroke-width="${edge.kind === 'sequence' ? 2.5 : 1.5}" stroke-dasharray="${edge.kind === 'relationship' ? '6 4' : ''}" marker-end="url(#plot-arrow)"><title>${esc(edge.label || '')}</title></path>`; }).join('');
    const nodeMarkup = nodes.map(node => { const color = plotNodeColor(node); return `<g data-plot-node="${escAttr(node.id)}" transform="translate(${Number(node.x || 0)},${Number(node.y || 0)})" style="cursor:grab"><rect x="-92" y="-36" width="184" height="72" rx="12" fill="var(--bg-card)" stroke="${color}" stroke-width="${node.id === plotState.selectedId ? 3 : 1.5}"></rect><text x="0" y="-7" text-anchor="middle" fill="var(--text)" font-size="13" font-weight="650">${esc(plotNodeTitle(node))}</text><text x="0" y="15" text-anchor="middle" fill="${color}" font-size="11">${esc(plotKindLabels[node.kind || node.type] || node.kind || node.type || '节点')}</text><text x="0" y="29" text-anchor="middle" fill="var(--text-muted)" font-size="9">${esc(node.source === 'ai' ? 'AI 草稿' : node.metadata?.chapter ? `第${node.metadata.chapter}章` : '')}</text></g>`; }).join('');
    svg.innerHTML = `<defs><marker id="plot-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="var(--border)"></path></marker></defs><g id="plot-scene">${edgeMarkup}${nodeMarkup}</g>`;
    const scene = document.getElementById('plot-scene'); let tx = 0, ty = 0, scale = 1, panning = false, panX = 0, panY = 0, dragged = null;
    const update = () => scene.setAttribute('transform', `translate(${tx} ${ty}) scale(${scale})`);
    // Clone and replace to remove old listeners
    const newSvg = svg.cloneNode(false);
    newSvg.innerHTML = svg.innerHTML;
    svg.parentNode.replaceChild(newSvg, svg);
    const activeSvg = newSvg;
    activeSvg.addEventListener('wheel', event => { event.preventDefault(); scale = Math.max(.35, Math.min(2.4, scale * (event.deltaY < 0 ? 1.1 : .9))); update(); }, { passive: false });
    activeSvg.addEventListener('pointerdown', event => { const element = event.target.closest('[data-plot-node]'); if (element) { const node = allNodes.find(item => item.id === element.dataset.plotNode); if (node) { plotState.selectedId = node.id; renderPlotInspector(); dragged = { node, point: plotScenePoint(event, scene), moved: false }; activeSvg.setPointerCapture(event.pointerId); } return; } panning = true; panX = event.clientX - tx; panY = event.clientY - ty; activeSvg.setPointerCapture(event.pointerId); });
    activeSvg.addEventListener('pointermove', event => { if (dragged) { const point = plotScenePoint(event, scene); const dx = point.x - dragged.point.x, dy = point.y - dragged.point.y; if (Math.abs(dx) + Math.abs(dy) > 2) dragged.moved = true; dragged.node.x = Math.round(dragged.node.x + dx); dragged.node.y = Math.round(dragged.node.y + dy); dragged.point = point; const nodeElement = [...activeSvg.querySelectorAll('[data-plot-node]')].find(item => item.dataset.plotNode === dragged.node.id); if (nodeElement) nodeElement.setAttribute('transform', `translate(${dragged.node.x},${dragged.node.y})`); return; } if (panning) { tx = event.clientX - panX; ty = event.clientY - panY; update(); } });
    activeSvg.addEventListener('pointerup', () => { if (dragged) { const item = dragged; dragged = null; if (item.moved) savePlotDelta({ operations: [{ op: 'move_node', id: item.node.id, x: item.node.x, y: item.node.y }] }); } panning = false; });
    activeSvg.querySelectorAll('[data-plot-node]').forEach(element => element.addEventListener('click', () => { plotState.selectedId = element.dataset.plotNode; renderPlotInspector(); drawPlotGraph(); })); update();
  }
  function renderPlotForecastBranches(result) {
    const target = document.getElementById('plot-ai-results'); if (!target) return; const branches = result?.branches || []; window.__plotForecastBranches = branches;
    target.innerHTML = `<div class="card-title-row mt16"><h3>推演分支</h3><span class="dim-note">选择“加入画布”后才会成为剧情草稿节点</span></div>${branches.map((branch, index) => `<div class="card plot-branch-card"><div class="card-title-row"><h3>${index + 1}. ${esc(branch.title || branch.id)}</h3><button class="btn btn-sm btn-primary" onclick="applyPlotBranch(${index})">加入画布</button></div><p class="dim-note">${esc(branch.summary || '')}</p><div class="mt8">${(branch.plot_points || []).map((point, step) => `<div class="list-row"><span class="badge badge-muted">${step + 1}</span><span>${esc(point)}</span></div>`).join('')}</div>${(branch.risks || []).length ? `<div class="warn-banner mt8">风险：${esc(branch.risks.join('；'))}</div>` : ''}</div>`).join('') || '<div class="card"><p class="dim-note">模型没有返回分支。</p></div>'}`;
  }
  window.runPlotForecast = async function () {
    const target = document.getElementById('plot-ai-results'); const node = (plotState.graph?.nodes || []).find(item => item.id === plotState.selectedId); const currentChapter = Number(node?.metadata?.chapter || 0);
    try { const queued = await api('POST', `/books/${S.book}/forecast`, { branchCount: Number(document.getElementById('plot-branch-count')?.value || 3), currentChapter, depth: Number(document.getElementById('plot-depth')?.value || 3), context: document.getElementById('plot-ai-context')?.value || '', nodeId: plotState.selectedId || '', canvasRevision: plotState.revision }); const task = await waitForTask(queued.taskId, current => renderTaskState(target, current, '剧情工作流推演')); if (task.status === 'completed') renderPlotForecastBranches(task.result); } catch (error) { if (target) target.innerHTML = `<div class="warn-banner">剧情推演失败：${esc(error.message)}</div>`; }
  };
  window.applyPlotBranch = async function (index) {
    const branch = window.__plotForecastBranches?.[index]; if (!branch) return;
    try { const data = await api('POST', `/books/${S.book}/plot-canvas/apply-branch`, { branch, sourceNodeId: plotState.selectedId || '', expectedRevision: plotState.revision }); plotState.graph = data.graph; plotState.revision = data.revision; drawPlotGraph(); renderPlotInspector(); toast('AI 分支已作为草稿加入画布', 'success'); } catch (error) { await reloadPlotCanvas().catch(() => {}); drawPlotGraph(); renderPlotInspector(); toast(error.message, 'error'); }
  };
  PAGES.plot = async (p) => {
    if (!S.book) return go('dashboard'); await reloadPlotCanvas();
    let planningViews=[]; try { planningViews=(await api('GET',`/books/${S.book}/planning-views`)).views||[]; } catch (_) {}
    const planningStrip=`<div class="grid grid-4 mt16">${['mindmap','timeline','plot_workflow','character_relationships'].map(key=>{const view=planningViews.find(item=>item.view_type===key), payload=view?.payload||{}; return `<button class="card" style="text-align:left;cursor:pointer" onclick="go('planning')"><div class="card-title-row"><h3>${planningViewLabel(key)}</h3><span class="badge badge-muted">只读</span></div><p class="dim-note">${Number(payload.nodes?.length||0)} 个节点 · ${Number(payload.edges?.length||0)} 条关系</p></button>`;}).join('')}</div>`;
    p.innerHTML = header('剧情工作流 / 交互画布', '自动规划资产只读；这里的拖拽、增删、隐藏和 AI 分支都属于预测草稿，不会自动进入章节参考', `<button class="btn btn-secondary" onclick="go('planning')">查看自动规划</button><button class="btn btn-secondary" onclick="go('forecast')">独立推演页</button><button class="btn btn-secondary" onclick="reloadPlotCanvas().then(()=>{drawPlotGraph();renderPlotInspector()})">刷新画布</button>`) + `<div class="content"><div class="card plot-toolbar"><div class="row row-wrap"><b>画布视图</b><button class="btn btn-sm ${plotState.view === 'timeline' ? 'btn-primary' : 'btn-ghost'}" onclick="setPlotView('timeline')">时间线</button><button class="btn btn-sm ${plotState.view === 'relations' ? 'btn-primary' : 'btn-ghost'}" onclick="setPlotView('relations')">关系线</button><button class="btn btn-sm ${plotState.view === 'all' ? 'btn-primary' : 'btn-ghost'}" onclick="setPlotView('all')">全部</button><button class="btn btn-sm btn-secondary" onclick="addPlotNote()">新增节点</button><button class="btn btn-sm btn-ghost" onclick="togglePlotHidden()">${plotState.showHidden ? '隐藏已隐藏节点' : '显示已隐藏节点'}</button><span class="spacer"></span><span class="badge badge-muted">revision ${plotState.revision}</span></div></div><div class="warn-banner">自动生成的思维导图、时间轴、剧情工作流、人物关系图不可直接修改；本画布是独立推演层，满意后请用“加入画布”显式采纳。</div>${planningStrip}<div class="plot-layout"><div class="card plot-canvas-card"><svg id="plot-svg" style="width:100%;height:68vh;background:var(--bg);border-radius:var(--radius-sm);touch-action:none"></svg></div><div><div id="plot-detail" class="card"></div><div class="card mt16"><h3>AI 剧情推演</h3><p class="dim-note mt8">模型会读取当前完整画布和选中节点；调整内容只用于本次预测，不写入后续章节参考。</p><div class="grid grid-2 mt8"><label class="fld">分支数<input class="input" id="plot-branch-count" type="number" value="3" min="1" max="8"></label><label class="fld">深度<input class="input" id="plot-depth" type="number" value="3" min="1" max="12"></label></div><label class="fld">额外指导<textarea class="input textarea" id="plot-ai-context" placeholder="希望推进的冲突、必须避开的结果…"></textarea></label><button class="btn btn-primary" onclick="runPlotForecast()">从选中节点推演</button></div></div></div><div id="plot-ai-results"></div></div>`;
    drawPlotGraph(); renderPlotInspector();
  };
  let planningViewKey = 'mindmap';
  function planningViewLabel(key) { return ({mindmap:'思维导图',timeline:'故事时间轴',plot_workflow:'剧情工作流',character_relationships:'人物关系'})[key] || key; }
  PAGES.planning = async (p) => {
    if (!S.book) return go('dashboard');
    try {
      const [data, book] = await Promise.all([api('GET', `/books/${S.book}/planning-views`), api('GET', `/books/${S.book}`)]); const views = data.views || [];
      const active = views.find(view => view.view_type === planningViewKey) || views[0];
      p.innerHTML = header('规划总览', '完整 25 步清单发布后，这里展示卷、故事弧、章节计划及四类只读结构示意图', `<button class="btn btn-primary" onclick="completePlanningImport()">进入 25 步规划审阅</button><button class="btn btn-secondary" onclick="go('plot')">打开交互画布</button><button class="btn btn-secondary" onclick="generatePlanningViews()">AI 重新整理</button>`) + `<div class="content"><div class="info-banner"><b>规划覆盖：</b><span>${esc(planningReadinessSummary(book.planningReadiness) || '尚未开始规划')}</span><button class="btn btn-sm btn-secondary" onclick="go('wizard')">打开卷 / 弧 / 章节计划</button></div><div class="warn-banner">这些视图用于理解作品全貌；“进入 25 步规划审阅”只会准备草稿，不会确认或发布任何步骤。审阅完成后，请从世界观向导逐步确认。</div><div class="row row-wrap mb16">${['mindmap','timeline','plot_workflow','character_relationships'].map(key => `<button class="btn btn-sm ${planningViewKey === key ? 'btn-primary' : 'btn-ghost'}" onclick="planningViewKey='${key}';go('planning')">${planningViewLabel(key)}</button>`).join('')}</div><div class="grid grid-2">${views.map(view => { const payload=view.payload||{}; const nodes=payload.nodes||[], edges=payload.edges||[]; return `<div class="card"><div class="card-title-row"><div><h3>${esc(planningViewLabel(view.view_type))}</h3><p class="dim-note">${nodes.length} 个节点 · ${edges.length} 条关系 · 第 ${esc(view.version || '—')} 版</p></div><span class="badge badge-muted">只读示意</span></div><div class="planning-node-list">${nodes.slice(0,12).map(node => `<div class="list-row"><span class="badge badge-info">${esc(node.kind||node.type||'节点')}</span><span>${esc(node.title||node.label||node.id)}</span></div>`).join('') || '<p class="dim-note">当前资料还没有可识别的节点。</p>'}</div><p class="dim-note mt8">来源：${esc((view.source_manifest||[]).map(item=>item.filename).join('、')||'尚未导入文件')}；已整理 ${edges.length} 条关系。</p></div>`; }).join('')}</div><div id="planning-task-state" class="mt16"></div></div>`;
      if (active) { const card=[...p.querySelectorAll('.card')].find(item => item.querySelector('h3')?.textContent === planningViewLabel(active.view_type)); card?.classList.add('planning-view-active'); }
    } catch (error) { renderFeatureEmpty(p, '自动规划视图', error.message, '<button class="btn btn-primary" onclick="go(\'create\')">返回新建作品</button>'); }
   };
   const legacyPlanningView = PAGES.planning;
   PAGES.planning = async (p) => {
     await legacyPlanningView(p);
     // The base planning view is useful for node discovery, but exposing the
     // storage payload as JSON is not useful to writers. Keep source manifest
     // and node summaries visible while removing the raw storage envelope.
     p.querySelectorAll('details').forEach((details) => {
       if ((details.textContent || '').includes('查看结构数据')) details.remove();
     });
     p.querySelectorAll('.planning-node-list .badge-info').forEach((badge) => {
       const key = (badge.textContent || '').trim();
       if (typeof nodeKindLabel === 'function') badge.textContent = nodeKindLabel(key);
     });
   };
   window.generatePlanningViews = async function () {
    const target=document.getElementById('planning-task-state'); if(target)target.innerHTML='<div class="card"><p class="dim-note">正在排队 AI 规划视图任务…</p></div>';
    try { const queued=await api('POST',`/books/${S.book}/planning-views/generate`,{}); const task=await waitForTask(queued.taskId,current=>renderTaskState(target,current,'自动规划视图')); if(task.status==='completed')go('planning'); } catch(error) { if(target)target.innerHTML=`<div class="warn-banner">${esc(error.message)}</div>`; }
  };
  window.completePlanningImport = async function () {
    try { await api('POST',`/books/${S.book}/planning-sources/prepare`,{}); toast('已准备 25 步规划草稿，请逐步审阅确认','success'); go('wizard'); } catch(error) { toast(error.message,'error'); }
  };
  window.continuousAuthorDecision = async function (decision) {
    const taskId = enhancedContinuousTask || localStorage.getItem('novelforge-continuous-' + S.book);
    if (!taskId) return toast('没有可操作的连续创作任务', 'warning');
    const reason = decision === 'accept' ? (window.prompt('请记录作者放行理由') || '') : '';
    try {
      await api('POST', `/tasks/${taskId}/author-decision`, { decision, reason });
      await reloadContinuousStatus();
      continuousPoll(taskId);
      toast('作者决策已记录，任务将从安全检查点恢复', 'success');
    } catch (error) { toast(error.message, 'error'); }
  };
  PAGES.thought = async (p) => {
    if (!S.book) return go('dashboard');
    try {
      const session=await api('GET',`/books/${S.book}/thought-session?optional=true`);
      if(session.exists===false){
        const [wizardState,book]=await Promise.all([api('GET',`/books/${S.book}/wizard/state`),api('GET',`/books/${S.book}`)]);
        const steps=wizardState.steps?.length?wizardState.steps:Array.from({length:25},(_,index)=>({number:index+1,label:`第 ${index+1} 步`,status:'pending'}));
        const confirmed=steps.filter(item=>item.status==='confirmed').length;
        const checklist=steps.map(step=>`<div class="list-row"><span class="wizard-step-number ${step.status==='confirmed'?'done':''}">${step.number}</span><span>${esc(step.label||step.key)}</span><span class="spacer"></span><span class="badge ${step.status==='confirmed'?'badge-success':step.status==='draft'?'badge-warning':'badge-muted'}">${step.status==='confirmed'?'已确认':step.status==='draft'?'待审阅':'待填写'}</span></div>`).join('');
        p.innerHTML=header('念头创作','从一个念头开始，通过 AI 追问完成完整的 25 步 Story Bible，并补齐卷、弧、章节目标计划',`<button class="btn btn-secondary" onclick="go('chat')">与 AI 助手对话</button><button class="btn btn-primary" onclick="go('create')">新建念头作品</button>`) + `<div class="content">${book.planningReadiness&&!book.planningReadiness.ready?planningGateMarkup(book.planningReadiness,'thought'):''}<div class="info-banner"><b>开始前请完成完整清单：</b><span>${confirmed}/25 已确认</span><span class="dim-note">AI 会逐步追问；每一卷、每一段故事弧、每一章的目标计划也必须完成后，正文创作才会解锁。</span></div><div class="grid grid-2"><div class="card"><div class="card-title-row"><h3>25 步向导清单</h3><span class="badge badge-info">${confirmed}/25</span></div>${checklist}</div><div class="card"><h3>从念头开始</h3><p style="line-height:1.8">先输入一个冲突、人物、画面或问题，创建后进入 AI 追问。每次回答都会保存为任务记录，并把内容放入可审阅的 Story Bible 草稿。</p><div class="row row-wrap mt16"><button class="btn btn-primary" onclick="go('create')">新建念头作品</button><button class="btn btn-ghost" onclick="go('chat')">先与 AI 助手讨论</button></div></div></div></div>`;
        return;
      }
      const [wizardState,book]=await Promise.all([api('GET',`/books/${S.book}/wizard/state`),api('GET',`/books/${S.book}`)]);
      const turns=session.turns||[];
      const steps=wizardState.steps||[];
      const confirmed=steps.filter(item=>item.status==='confirmed').length;
      const readiness=book.planningReadiness||null;
      const checklist=steps.map(step=>`<div class="list-row"><span class="wizard-step-number ${step.status==='confirmed'?'done':''}">${step.number}</span><span>${esc(step.label||step.key)}</span><span class="spacer"></span><span class="badge ${step.status==='confirmed'?'badge-success':step.status==='draft'?'badge-warning':'badge-muted'}">${step.status==='confirmed'?'已确认':step.status==='draft'?'待审阅':'待填写'}</span></div>`).join('');
      p.innerHTML=header('念头创作', '从一个念头开始，通过 AI 追问和 25 步清单形成可执行的完整小说框架', `<button class="btn btn-secondary" onclick="go('chat')">与 AI 助手对话</button><button class="btn btn-secondary" onclick="go('wizard')">打开 25 步向导</button><button class="btn btn-primary" onclick="generateThoughtFramework()">生成完整框架</button>`) + `<div class="content">${readiness&&!readiness.ready?planningGateMarkup(readiness,'thought'):''}<div class="info-banner"><b>25 步清单进度：</b><span>${confirmed}/25 已确认</span><span class="dim-note">${esc(planningReadinessSummary(readiness)||'先完成访谈，再逐步确认 Story Bible')}</span></div><div class="grid grid-2"><div><div class="card"><div class="card-title-row"><h3>原始念头</h3><span class="badge badge-info">${esc(session.status||'questioning')}</span></div><p style="white-space:pre-wrap;line-height:1.8">${esc(session.seed||'—')}</p></div><div class="card"><div class="card-title-row"><h3>25 步向导清单</h3><span class="badge badge-info">${confirmed}/25</span></div>${checklist||'<p class="dim-note">正在初始化清单…</p>'}</div><div class="card"><h3>访谈记录</h3><div>${turns.map(turn => `<div class="list-row" style="align-items:flex-start"><span class="badge ${turn.role==='assistant'?'badge-info':'badge-success'}">${turn.role==='assistant'?'AI':'你'}</span><span style="white-space:pre-wrap;line-height:1.7">${esc(turn.content||'')}</span></div>`).join('')}</div></div></div><div><div class="card"><h3>继续回答</h3><p class="dim-note">问题 ${Number(session.question_index||0)+1} · 每次只回答当前问题即可。</p><div class="card mt16" style="background:var(--bg)"><p style="white-space:pre-wrap;line-height:1.8">${esc(session.current_question||'请继续补充这个故事。')}</p></div><textarea class="input textarea" id="thought-answer" placeholder="想到什么就写什么，不需要先整理成大纲"></textarea><button class="btn btn-primary mt8" onclick="submitThoughtAnswer()">回答并继续追问</button>${session.error?`<div class="warn-banner mt16">上次任务失败：${esc(session.error)}。可以修正模型配置后重新回答。</div>`:''}</div><div id="thought-task-state"></div><div class="card"><h3>下一步</h3><p class="dim-note" style="line-height:1.8">AI 生成的框架只会进入 25 步 Story Bible 草稿。你必须回到向导逐步审阅、修改并确认；同时补齐每一卷、每一段故事弧、每一章的目标计划后，正文创作才会解锁。</p><div class="row row-wrap mt16"><button class="btn btn-secondary" onclick="go('wizard')">继续确认清单</button><button class="btn btn-ghost" onclick="go('chat')">继续和 AI 助手讨论</button></div></div></div></div></div>`;
    } catch(error) { renderFeatureEmpty(p,'念头创作',error.message,'<button class="btn btn-primary" onclick="go(\'create\')">新建念头作品</button>'); }
  };
  window.submitThoughtAnswer = async function () {
    const answer=document.getElementById('thought-answer')?.value.trim(); if(!answer)return toast('先写下你的回答','error');
    const target=document.getElementById('thought-task-state');
    try { const queued=await api('POST',`/books/${S.book}/thought-session/respond`,{answer}); const task=await waitForTask(queued.taskId,current=>renderTaskState(target,current,'念头追问')); if(task.status==='completed')go('thought'); } catch(error) { if(target)target.innerHTML=`<div class="warn-banner">${esc(error.message)}</div>`; }
  };
  window.generateThoughtFramework = async function () {
    const target=document.getElementById('thought-task-state');
    try { const queued=await api('POST',`/books/${S.book}/thought-session/framework`,{}); const task=await waitForTask(queued.taskId,current=>renderTaskState(target,current,'生成完整小说框架')); if(task.status==='completed'){toast('框架已进入 Story Bible 草稿','success');go('wizard');} } catch(error) { if(target)target.innerHTML=`<div class="warn-banner">${esc(error.message)}</div>`; }
  };
  PAGES['world-map'] = async (p) => {
    if (!S.book) return go('dashboard');
    const book = await api('GET', `/books/${S.book}`);
    const locations = Object.entries(book.locations || {});
    p.innerHTML = header('世界地图', `${esc(bookName())} · 地点层级与连接关系`, `<button class="btn btn-secondary" onclick="go('wizard')">补充地点设定</button><button class="btn btn-secondary" onclick="go('characters')">人物关系</button>`) +
      `<div class="content"><div class="info-banner"><b>这张地图展示什么：</b><span>它展示地点的层级、连接和意义，不是美术地图。先把地点写进向导，地图才有可靠内容。</span><span class="badge badge-info">${locations.length} 个地点</span></div>
      ${locations.length ? `<div class="card" style="padding:0;overflow:hidden"><iframe title="世界地图" src="/api/v1/books/${S.book}/world-map" style="width:100%;height:68vh;border:none;background:#08111f"></iframe></div>` : `<div class="card empty-state"><h3>还没有地点</h3><p>在“地点”步骤中至少添加一个地点，再回来查看层级与连接关系。</p><button class="btn btn-primary mt16" onclick="go('wizard')">去补充地点</button></div>`}
      </div>`;
  };

  // ========== Interactive relationship graph ==========
  function drawFlowGraph(data) {
    const svg = document.getElementById('flow-svg');
    const detail = document.getElementById('flow-detail');
    if (!svg) return;
    const nodes = data.nodes || [];
    const width = Math.max(900, Math.min(1600, 260 * Math.max(3, nodes.length)));
    const height = Math.max(560, Math.ceil(nodes.length / 4) * 130);
    const columns = [...new Set(nodes.map((node) => node.type))];
    const positions = {};
    nodes.forEach((node, index) => {
      const column = Math.max(0, columns.indexOf(node.type));
      const sameType = nodes.filter((item) => item.type === node.type);
      const row = sameType.findIndex((item) => item.id === node.id);
      positions[node.id] = { x: 130 + column * 245, y: 70 + row * 115 };
    });
    const edgeMarkup = (data.edges || []).map((edge) => { const source = positions[edge.source]; const target = positions[edge.target]; if (!source || !target) return ''; const dx = (target.x - source.x) * .42; const path = `M ${source.x} ${source.y} C ${source.x + dx} ${source.y}, ${target.x - dx} ${target.y}, ${target.x} ${target.y}`; return `<path d="${path}" fill="none" stroke="var(--border)" stroke-width="1.5" marker-end="url(#flow-arrow)"><title>${esc(edge.label || '')}</title></path>`; }).join('');
    const nodeMarkup = nodes.map((node) => { const point = positions[node.id]; const typeLabel = typeof nodeKindLabel === 'function' ? nodeKindLabel(node.type) : node.type; return `<g class="flow-node" data-node-id="${escAttr(node.id)}" transform="translate(${point.x},${point.y})" style="cursor:pointer"><circle r="28" fill="var(--bg-card)" stroke="var(--accent)" stroke-width="2"></circle><text text-anchor="middle" y="4" fill="var(--text)" font-size="11">${esc((node.label || node.id).slice(0, 12))}</text><text text-anchor="middle" y="48" fill="var(--text-muted)" font-size="10">${esc(typeLabel)}</text></g>`; }).join('');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.innerHTML = `<defs><marker id="flow-arrow" markerWidth="8" markerHeight="8" refX="8" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="var(--border)"></path></marker></defs><g id="flow-scene">${edgeMarkup}${nodeMarkup}</g>`;
    const scene = document.getElementById('flow-scene');
    let tx = 0; let ty = 0; let scale = 1; let dragging = false; let startX = 0; let startY = 0;
    const updateTransform = () => { scene.setAttribute('transform', `translate(${tx} ${ty}) scale(${scale})`); };
    svg.addEventListener('wheel', (event) => { event.preventDefault(); scale = Math.max(0.35, Math.min(2.5, scale * (event.deltaY < 0 ? 1.1 : 0.9))); updateTransform(); }, { passive: false });
    svg.addEventListener('pointerdown', (event) => { if (event.target.closest('.flow-node')) return; dragging = true; startX = event.clientX - tx; startY = event.clientY - ty; svg.setPointerCapture(event.pointerId); });
    svg.addEventListener('pointermove', (event) => { if (!dragging) return; tx = event.clientX - startX; ty = event.clientY - startY; updateTransform(); });
    svg.addEventListener('pointerup', () => { dragging = false; });
    svg.querySelectorAll('.flow-node').forEach((element) => element.addEventListener('click', () => { const node = nodes.find((item) => item.id === element.dataset.nodeId); if (detail && node) { const typeLabel = typeof nodeKindLabel === 'function' ? nodeKindLabel(node.type) : node.type; detail.innerHTML = `<h3>${esc(node.label)}</h3><p class="dim-note">类型：${esc(typeLabel)}</p><p style="line-height:1.7;margin-top:8px">${esc(node.description || '无描述')}</p><details class="mt8"><summary>实体字段</summary><div class="mt8">${readableMetadata(node.metadata)}</div></details>`; } }));
  }
  PAGES.flow = async (p) => { const data = await api('GET', `/books/${S.book}/flow`); p.innerHTML = header('交互式关系图', '数据库事实关系用于理解全局；需要拖拽、增删、隐藏和剧情推演时进入统一交互画布', `<button class="btn btn-primary" onclick="go('plot')">打开统一交互画布</button><button class="btn btn-secondary" onclick="go('planning')">自动规划视图</button><button class="btn btn-secondary" onclick="go('flow')">刷新</button>`) + `<div class="content"><div class="warn-banner">四类自动规划图由 AI 读取全部规划资料后生成并保持只读。统一交互画布会把关系、时间线和预测分支放在同一可调整空间。</div><div class="card" style="padding:8px;overflow:hidden"><svg id="flow-svg" style="width:100%;height:66vh;background:var(--bg);border-radius:var(--radius-sm);touch-action:none"></svg></div><div id="flow-detail" class="card"><p class="dim-note">点击节点查看实体详情。</p></div></div>`; drawFlowGraph(data); };

  // The legacy characters page rendered Mermaid directly. Keep the original
  // visualization available through the flow page, but make this entry point
  // work when the optional CDN is unavailable and expose the same persisted
  // character records used by the interactive graph.
  // The main Studio page reads the canonical character tables. The old flow
  // projection is retained as an explicit advanced page so an empty flow
  // projection cannot hide characters already extracted into the work.
  PAGES.legacyCharacters = async (p) => {
    const data = await api('GET', `/books/${S.book}/flow`);
    const characters = (data.nodes || []).filter((node) => node.type === 'character');
    p.innerHTML = header('人物关系', '从 SQLite 关系投影读取人物资料；详细关系图支持缩放、拖拽和节点检查', '<button class="btn btn-primary" onclick="go(\'flow\')">打开交互式关系图</button>') + `<div class="content"><div class="grid grid-2">${characters.map((node) => `<div class="card"><div class="card-title-row"><h3>${esc(node.label || node.id)}</h3><span class="badge badge-info">人物</span></div><p class="dim-note">${esc(node.description || '暂无简介')}</p><details class="mt8"><summary>实体字段</summary><div class="mt8">${readableMetadata(node.metadata)}</div></details></div>`).join('') || '<div class="card"><p class="dim-note">暂无人物实体，请先在作品资料中创建人物。</p></div>'}</div></div>`;
  };

  window.saveProjectSettings = async function () {
    if (!S.book) return;
    try {
      const styleInput = document.getElementById('project-setting-style')?.value.trim() || '';
      const intentInput = document.getElementById('project-setting-intent')?.value.trim() || '';
      await api('PUT', '/books/' + encodeURIComponent(S.book), {
        title: document.getElementById('project-setting-title')?.value.trim() || '',
        genre: document.getElementById('project-setting-genre')?.value.trim() || '',
        targetVolumes: Number(document.getElementById('project-setting-volumes')?.value || 5),
        writingStyle: styleInput || document.getElementById('project-setting-style-source')?.value || '',
        authorIntent: intentInput || document.getElementById('project-setting-intent-source')?.value || '',
        styleProfile: (() => { try { return JSON.parse(document.getElementById('project-setting-style-profile')?.value || '{}'); } catch (_) { throw new Error('文风配置暂时无法读取，请刷新后重试'); } })()
      });
      S.books = [];
      toast('Project settings saved', 'success');
      await render();
      renderNav();
    } catch (error) { toast(error.message, 'error'); }
  };
  function projectExtensionScope(item) {
    const value = item.projectOverride === null || item.projectOverride === undefined ? '' : String(Boolean(item.projectOverride));
    return '<select class="input project-extension-scope" data-extension-type="' + escAttr(item.extensionType) + '" data-extension-id="' + escAttr(item.id) + '" style="max-width:150px">' +
      '<option value=""' + (value === '' ? ' selected' : '') + '>跟随全局</option>' +
      '<option value="true"' + (value === 'true' ? ' selected' : '') + '>本作品启用</option>' +
      '<option value="false"' + (value === 'false' ? ' selected' : '') + '>本作品停用</option></select>';
  }
  window.saveProjectExtensionOverrides = async function () {
    const payload = { skills: {}, mcpServers: {} };
    document.querySelectorAll('.project-extension-scope').forEach((element) => {
      const type = element.dataset.extensionType === 'mcp' ? 'mcpServers' : 'skills';
      payload[type][element.dataset.extensionId] = element.value === '' ? null : element.value === 'true';
    });
    try {
      await api('PUT', '/books/' + encodeURIComponent(S.book) + '/extensions', payload);
      toast('本作品的 Skill / MCP 开关已保存', 'success');
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };
  PAGES['project-settings'] = async (p) => {
    if (!S.book) return go('dashboard');
    const [book, extensions, planning] = await Promise.all([
      api('GET', '/books/' + encodeURIComponent(S.book)),
      api('GET', '/books/' + encodeURIComponent(S.book) + '/extensions'),
      api('GET', '/books/' + encodeURIComponent(S.book) + '/planning-summary').catch(() => ({ status: 'not_started' }))
    ]);
    const synthesis = planning.summary || {};
    const synthesizedStyle = synthesis.writing_style || {};
    const synthesizedStyleText = synthesizedStyle.summary || [synthesizedStyle.voice, synthesizedStyle.pov, synthesizedStyle.rhythm].filter(Boolean).join('；');
    const styleText = synthesizedStyleText || book.writingStyleDraft || book.writingStyle || '';
    const intentText = synthesis.author_intent || book.authorIntentDraft || book.authorIntent || '';
    const planningLabel = planning.status === 'ready' ? 'AI 已完成资料理解' : planning.status === 'needs_review' ? '资料理解完成，等待复核' : planning.status === 'running' || planning.status === 'queued' ? '资料理解任务进行中' : '尚未完成资料理解';
    const extensionRows = (extensions.skills || []).map((item) => ({ ...item, extensionType: 'skill', kind: 'Skill' }))
      .concat((extensions.mcpServers || []).map((item) => ({ ...item, extensionType: 'mcp', kind: 'MCP' })));
    const extensionHtml = extensionRows.map((item) => '<div class="list-row"><div><b>' + esc(item.name) + '</b><div class="dim-note">' + esc(item.kind) + ' · ' + esc(item.description || item.transport || '全局配置') + '</div></div><span class="spacer"></span>' + projectExtensionScope(item) + '</div>').join('') || '<p class="dim-note">还没有全局 Skill 或 MCP。请先进入“模型 / Skill / MCP”进行配置。</p>';
    p.innerHTML = header('作品设置', esc(book.title || S.book), '<button class="btn btn-primary" onclick="saveProjectSettings()">保存作品设置</button>') +
      '<div class="content"><div class="info-banner" style="max-width:840px"><b>' + esc(planningLabel) + '：</b><span>这里展示 AI 理解后的文风和作者意图摘要，不会把上传文件原文直接塞进表单。需要保留未整理资料时，系统仍会在后台留存来源。作者修改的规划字段会保存为 Story Bible 草稿，发布前不会改变 Canon。</span></div><div class="card" style="max-width:840px"><div class="grid grid-3"><label class="fld">标题<input class="input" id="project-setting-title" value="' + escAttr(book.title || '') + '"></label><label class="fld">题材<input class="input" id="project-setting-genre" value="' + escAttr(book.genre || '') + '"></label><label class="fld">目标卷数<input class="input" id="project-setting-volumes" type="number" min="1" value="' + escAttr(book.targetVolumes || 5) + '"></label></div><input type="hidden" id="project-setting-style-source" value="' + escAttr(book.writingStyleDraft || book.writingStyle || '') + '"><input type="hidden" id="project-setting-intent-source" value="' + escAttr(book.authorIntentDraft || book.authorIntent || '') + '"><label class="fld">AI 理解后的写作风格摘要<textarea class="input textarea" id="project-setting-style" placeholder="例如：冷静克制的近距离第三人称，情绪通过动作和物件呈现…">' + esc(styleText) + '</textarea></label><label class="fld">作者意图摘要<textarea class="input textarea" id="project-setting-intent" placeholder="这部作品想让读者持续追问什么？">' + esc(intentText) + '</textarea></label><details class="mt8"><summary>高级：结构化文风配置（可选）</summary><p class="dim-note mt8">供熟悉 JSON 的作者微调；普通创作不需要编辑这里。</p><textarea class="input textarea mono mt8" id="project-setting-style-profile" style="min-height:220px">' + esc(JSON.stringify(book.styleProfileDraft || book.styleProfile || {}, null, 2)) + '</textarea></details><p class="dim-note">写作、审查和剧情推演会读取上面的摘要与结构化约束。</p></div><div class="card"><div class="card-title-row"><div><h3>本作品可用的 Skill / MCP</h3><p class="dim-note">它们在“全局 AI 配置”中只需定义一次；这里仅决定本作品是否启用。跟随全局表示使用全局默认状态。</p></div><button class="btn btn-primary" onclick="saveProjectExtensionOverrides()">保存本作品开关</button></div>' + extensionHtml + '<div class="warn-banner" style="margin-top:12px">Skill 当前会作为 AI 对话和 Agent 的额外指令使用；MCP 当前保存并校验连接定义，真实 MCP 握手/工具调用尚未接入。</div></div></div>';
  };

  const readableProjectSettingsPage = PAGES['project-settings'];
  PAGES['project-settings'] = async (p) => {
    await readableProjectSettingsPage(p);
    const profile = p.querySelector('#project-setting-style-profile');
    const advanced = profile?.closest('details');
    if (advanced) advanced.hidden = true;
    if (profile) profile.setAttribute('aria-hidden', 'true');
  };

  // ========== Structured interactive film studio ==========
  function filmAssetUrl(assetRef) {
    const parts = String(assetRef || '').split('/');
    if (parts.length < 4 || parts[0] !== 'interactive-films' || parts[2] !== 'assets') return '';
    return '/api/v1/interactive-films/assets/' + encodeURIComponent(parts[1]) + '/' + parts.slice(3).map(encodeURIComponent).join('/');
  }
  function filmChoicesText(choices) {
    return (choices || []).map((choice, index) => {
      const text = choice.text || choice.label || choice.id || `选项 ${index + 1}`;
      const target = choice.targetNodeId ? ` → ${choice.targetNodeId}` : '';
      const effects = choice.effects ? `；效果：${taskValueText(choice.effects)}` : '';
      return text + target + effects;
    }).join('\n');
  }
  function parseFilmChoices(value, previous) {
    return String(value || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line, index) => {
      const old = previous?.[index] || {};
      const effectAt = line.indexOf('；效果：');
      const effectText = effectAt >= 0 ? line.slice(effectAt + 5).trim() : '';
      const main = effectAt >= 0 ? line.slice(0, effectAt).trim() : line;
      const arrowAt = main.indexOf('→');
      const text = (arrowAt >= 0 ? main.slice(0, arrowAt) : main).trim();
      const targetNodeId = (arrowAt >= 0 ? main.slice(arrowAt + 1) : old.targetNodeId || '').trim();
      const next = { ...old, id: old.id || `choice_${Date.now().toString(36)}_${index}`, text, targetNodeId };
      if (effectText) next.effects = effectText;
      return next;
    });
  }
  async function downloadStudioRoute(path, filename) {
    const response = await fetch('/api/v1' + path);
    if (!response.ok) {
      const detail = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(typeof detail.detail === 'string' ? detail.detail : detail.detail?.message || response.statusText);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }
  function filmNodeEditor(node) {
    const id = escAttr(node.id);
    return '<div class="card" data-film-node="' + id + '">' +
      '<div class="card-title-row"><div><span class="badge badge-info">' + esc(node.type) + '</span><b style="margin-left:8px">' + esc(node.title || node.id) + '</b></div>' +
      '<button class="btn btn-sm btn-secondary" onclick="saveFilmNode(&quot;' + id + '&quot;)">保存节点</button></div>' +
      '<div class="grid grid-2"><label class="fld">节点标题<input class="input" id="film-title-' + id + '" value="' + escAttr(node.title || '') + '"></label>' +
      '<label class="fld">节点类型<select class="input" id="film-type-' + id + '">' +
      ['start', 'normal', 'branch', 'merge', 'ending', 'explore'].map((type) => '<option value="' + type + '"' + (node.type === type ? ' selected' : '') + '>' + type + '</option>').join('') +
      '</select></label></div>' +
      '<label class="fld">场景描述<textarea class="input textarea" id="film-scene-' + id + '">' + esc(node.sceneDesc || '') + '</textarea></label>' +
      '<label class="fld">选项与效果 <span class="dim-note">每行一个；可用“选项文字 → 节点编号”连接下一节点</span><textarea class="input textarea" id="film-choices-' + id + '" style="min-height:140px" placeholder="例如：留下来调查 → node_abc\n例如：立即离开">' + esc(filmChoicesText(node.choices || [])) + '</textarea></label>' +
      '<div class="row row-wrap"><button class="btn btn-sm btn-secondary" onclick="generateFilmNodeImage(&quot;' + id + '&quot;)">生成节点配图</button>' +
      '<span class="dim-note">' + (node.imageSlot?.assetRef ? '已有图片资产' : '尚无图片资产') + '</span></div>' +
      (node.imageSlot?.assetRef ? '<img src="' + filmAssetUrl(node.imageSlot.assetRef) + '" alt="" style="max-width:180px;border-radius:8px;margin-top:10px">' : '') +
       '</div>';
  }
  const FILM_PHASES = [
    ['world', 'World'],
    ['scale', 'Scale'],
    ['structure', 'Structure'],
    ['workshop', 'Nodes'],
    ['validate', 'Validate']
  ];
  window.openInteractiveFilmChat = function () {
    chatMode = 'interactive-film';
    localStorage.setItem('novelforge-chat-mode', chatMode);
    go('chat');
  };
  function filmPhaseStatuses(graph, validation) {
    const contentNodes = (graph.nodes || []).filter((node) => node.type !== 'ending');
    const filledNodes = contentNodes.filter((node) => String(node.sceneDesc || '').trim() || (node.dialogue || []).length);
    const hasStart = (graph.nodes || []).some((node) => node.type === 'start');
    const hasEnding = (graph.nodes || []).some((node) => node.type === 'ending');
    const hasEdge = (graph.nodes || []).some((node) => (node.choices || []).length > 0);
    return {
      world: graph.worldAnchor?.storyCore && (graph.characters || []).length ? 'done' : graph.worldAnchor?.storyCore ? 'partial' : 'empty',
      scale: graph.scale && Object.keys(graph.scale).length ? 'done' : 'empty',
      structure: hasStart && hasEnding && hasEdge ? 'done' : (graph.nodes || []).length ? 'partial' : 'empty',
      workshop: !contentNodes.length ? 'empty' : filledNodes.length === contentNodes.length ? 'done' : filledNodes.length ? 'partial' : 'empty',
      validate: validation.ok ? 'done' : (graph.nodes || []).length && graph.endings?.length ? 'partial' : 'empty'
    };
  }
  function filmPhaseBar(graph, validation) {
    const active = localStorage.getItem('novelforge-film-phase-' + S.book) || 'world';
    const statuses = filmPhaseStatuses(graph, validation);
    return '<div class="card" style="margin-bottom:16px"><div class="row row-wrap">' + FILM_PHASES.map((phase, index) => {
      const status = statuses[phase[0]];
      const cls = active === phase[0] ? 'btn-primary' : 'btn-secondary';
      return '<button class="btn btn-sm ' + cls + '" onclick="setFilmPhase(\'' + phase[0] + '\')"><span class="badge badge-' + (status === 'done' ? 'success' : status === 'partial' ? 'warning' : 'muted') + '">' + (index + 1) + '</span> ' + esc(phase[1]) + ' · ' + esc(status) + '</button>';
    }).join('') + '</div></div>';
  }
  function filmPhasePanel(graph, validation, analysis) {
    const active = localStorage.getItem('novelforge-film-phase-' + S.book) || 'world';
    const panel = {
      world: '<p class="dim-note">Define the story core, world rules, genre, duration, and main characters. The durable authoring chat can refine this graph.</p><button class="btn btn-sm btn-secondary" onclick="openInteractiveFilmChat()">Open authoring chat</button>',
      scale: '<p class="dim-note">Set durable targets for the graph before expanding the workshop.</p><div class="grid grid-3"><label class="fld">Node target<input class="input" id="film-scale-node-target" type="number" min="1" max="1000" value="' + escAttr(graph.scale?.nodeTarget || analysis.distribution?.nodeCount || 1) + '"></label><label class="fld">Branch depth<input class="input" id="film-scale-branch-depth" type="number" min="0" max="20" value="' + escAttr(graph.scale?.branchDepth || 0) + '"></label><label class="fld">Ending target<input class="input" id="film-scale-ending-target" type="number" min="1" max="100" value="' + escAttr(graph.scale?.endingTarget || analysis.distribution?.endingCount || 1) + '"></label></div><button class="btn btn-sm btn-primary" onclick="saveFilmScale()">Save scale</button>',
      structure: '<p class="dim-note">Review the branching topology, dead ends, and ending reachability before polishing scenes.</p><button class="btn btn-sm btn-secondary" onclick="go(\'film-flow\')">Open story graph flow</button>',
      workshop: '<p class="dim-note">Edit scene descriptions, node types, choices, conditions, and effects below. Each save uses the graph revision to prevent silent overwrites.</p>',
      validate: '<p class="dim-note">Validation is computed from the persisted graph. Errors block a trustworthy playable/exportable result; warnings and image notices remain visible for author review.</p><span class="badge ' + (validation.ok ? 'badge-success' : 'badge-error') + '">' + (validation.ok ? 'Playable' : 'Needs repair') + '</span>'
    }[active] || '';
    return '<div class="card" style="margin-bottom:16px"><div class="card-title-row"><h3>' + esc((FILM_PHASES.find((item) => item[0] === active) || FILM_PHASES[0])[1]) + '</h3><span class="badge badge-info">Film Wizard</span></div>' + panel + '</div>';
  }
  window.setFilmPhase = function (phase) {
    if (!FILM_PHASES.some((item) => item[0] === phase)) return;
    localStorage.setItem('novelforge-film-phase-' + S.book, phase);
    render();
  };
  window.saveFilmScale = async function () {
    const graph = window.__filmGraph;
    if (!graph) return;
    const scale = {
      nodeTarget: Number(document.getElementById('film-scale-node-target')?.value || 0),
      branchDepth: Number(document.getElementById('film-scale-branch-depth')?.value || 0),
      endingTarget: Number(document.getElementById('film-scale-ending-target')?.value || 0)
    };
    if (!Number.isInteger(scale.nodeTarget) || scale.nodeTarget < 1 || !Number.isInteger(scale.branchDepth) || scale.branchDepth < 0 || !Number.isInteger(scale.endingTarget) || scale.endingTarget < 1) {
      return toast('Scale values must be valid integers', 'warning');
    }
    try {
      await api('POST', '/projects/' + encodeURIComponent(S.book) + '/story-graph/delta', { expectedRev: graph.revision, delta: { scale } });
      toast('Scale settings saved', 'success');
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };
  function filmFlowColor(type) {
    return ({ start: '#38bdf8', branch: '#f59e0b', ending: '#fb7185', merge: '#a78bfa', explore: '#c084fc', normal: '#94a3b8' })[type] || '#94a3b8';
  }
  function filmFlowPositions(nodes) {
    const positions = {};
    (nodes || []).forEach((node, index) => {
      const supplied = node.position || {};
      const x = Number(supplied.x);
      const y = Number(supplied.y);
      positions[node.id] = {
        x: Number.isFinite(x) ? Math.max(24, x) : 24 + (index % 3) * 330,
        y: Number.isFinite(y) ? Math.max(24, y) : 24 + Math.floor(index / 3) * 180
      };
    });
    return positions;
  }
  PAGES['film-flow'] = async (p) => {
    if (!S.book) return go('films');
    if (!await hasInteractiveFilm(S.book)) {
      renderFeatureEmpty(p, '互动影像流程', '当前作品还没有互动影像图谱。创建图谱后，这里会提供节点布局和关系检查。', '<button class="btn btn-primary" onclick="go(\'films\')">创建互动影像</button>');
      return;
    }
    let graph;
    try {
      graph = await api('GET', '/projects/' + encodeURIComponent(S.book) + '/story-graph');
    } catch (error) {
      if (isMissingFeatureError(error)) {
        renderFeatureEmpty(p, '互动影像流程', '当前作品还没有互动影像图谱。创建图谱后，这里会提供节点布局和关系检查。', '<button class="btn btn-primary" onclick="go(\'films\')">创建互动影像</button>');
        return;
      }
      throw error;
    }
    let positions = filmFlowPositions(graph.nodes);
    let drag = null;
    let suppressClick = false;
    p.innerHTML = header('Story Graph Flow', esc(graph.title || bookName()), '<button class="btn btn-secondary" onclick="go(\'film\')">Back to Studio</button><button class="btn btn-primary" onclick="go(\'story-player\')">Play</button>') +
      '<div class="content"><div class="warn-banner">Nodes are loaded from the persisted story graph. Dragging a node saves its position through the revisioned delta API.</div><div class="card" style="padding:8px;overflow:auto"><svg id="film-flow-svg" style="width:100%;min-height:70vh;background:var(--bg);border-radius:var(--radius-sm);touch-action:none"></svg></div><div id="film-flow-detail" class="card"><p class="dim-note">Click a node to inspect its scene, choices, and effects.</p></div></div>';
    const svg = document.getElementById('film-flow-svg');
    const detail = document.getElementById('film-flow-detail');
    if (!svg || !detail) return;
    const toSvgPoint = (event) => {
      const matrix = svg.getScreenCTM();
      if (!matrix) return null;
      const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
      return { x: point.x, y: point.y };
    };
    const renderFlow = () => {
      const nodeWidth = 230;
      const nodeHeight = 106;
      const height = Math.max(720, ...Object.values(positions).map((pos) => pos.y + nodeHeight + 80));
      svg.setAttribute('viewBox', '0 0 1100 ' + height);
      const nodeMap = Object.fromEntries((graph.nodes || []).map((node) => [node.id, node]));
      const edges = (graph.nodes || []).flatMap((node) => (node.choices || []).map((choice) => ({ node, choice, target: nodeMap[choice.targetNodeId] }))).filter((edge) => edge.target);
      const edgeHtml = edges.map(({ node, choice }) => {
        const from = positions[node.id];
        const to = positions[choice.targetNodeId];
        const x1 = from.x + nodeWidth;
        const y1 = from.y + nodeHeight / 2;
        const x2 = to.x;
        const y2 = to.y + nodeHeight / 2;
        return '<path d="M ' + x1 + ' ' + y1 + ' C ' + (x1 + 80) + ' ' + y1 + ', ' + (x2 - 80) + ' ' + y2 + ', ' + x2 + ' ' + y2 + '" fill="none" stroke="#64748b" stroke-width="2" marker-end="url(#film-arrow)"></path><text x="' + ((x1 + x2) / 2) + '" y="' + ((y1 + y2) / 2 - 6) + '" fill="#cbd5e1" font-size="12" text-anchor="middle">' + esc(choice.text || choice.id) + '</text>';
      }).join('');
      const nodeHtml = (graph.nodes || []).map((node) => {
        const pos = positions[node.id];
        const color = filmFlowColor(node.type);
        return '<g data-film-flow-node="' + escAttr(node.id) + '" transform="translate(' + pos.x + ' ' + pos.y + ')" style="cursor:grab"><rect width="' + nodeWidth + '" height="' + nodeHeight + '" rx="12" fill="#171b24" stroke="' + color + '" stroke-width="2"></rect><text x="16" y="26" fill="#f8fafc" font-size="15" font-weight="600">' + esc(node.title || node.id) + '</text><text x="16" y="49" fill="' + color + '" font-size="12">' + esc(node.type) + '</text><text x="16" y="72" fill="#94a3b8" font-size="11">' + esc((node.choices || []).length + ' choices') + '</text><text x="16" y="91" fill="#64748b" font-size="10">drag to persist position</text></g>';
      }).join('');
      svg.innerHTML = '<defs><marker id="film-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#64748b"></path></marker></defs>' + edgeHtml + nodeHtml;
      svg.querySelectorAll('[data-film-flow-node]').forEach((element) => {
        const nodeId = element.getAttribute('data-film-flow-node');
        const node = (graph.nodes || []).find((item) => item.id === nodeId);
        if (!node) return;
        element.addEventListener('click', () => {
          if (suppressClick) { suppressClick = false; return; }
          detail.innerHTML = '<div class="card-title-row"><h3>' + esc(node.title || node.id) + '</h3><span class="badge badge-info">' + esc(node.type) + '</span></div><p class="dim-note mt8">' + esc(node.sceneDesc || '暂无场景描述') + '</p><div class="mt8"><b>可选行动</b>' + ((node.choices || []).map((choice, index) => '<div class="list-row"><span class="badge badge-muted">' + (index + 1) + '</span><span>' + esc(filmChoicesText([choice])) + '</span></div>').join('') || '<p class="dim-note mt8">暂无可选行动。</p>') + '</div>';
        });
        element.addEventListener('pointerdown', (event) => {
          const point = toSvgPoint(event);
          if (!point) return;
          const pos = positions[node.id];
          drag = { nodeId: node.id, element, offsetX: point.x - pos.x, offsetY: point.y - pos.y, moved: false, pointerId: event.pointerId };
          element.setPointerCapture(event.pointerId);
        });
      });
    };
    svg.addEventListener('pointermove', (event) => {
      if (!drag) return;
      const point = toSvgPoint(event);
      if (!point) return;
      const next = positions[drag.nodeId];
      next.x = Math.max(24, point.x - drag.offsetX);
      next.y = Math.max(24, point.y - drag.offsetY);
      drag.moved = true;
      drag.element.setAttribute('transform', 'translate(' + next.x + ' ' + next.y + ')');
    });
    svg.addEventListener('pointerup', async () => {
      if (!drag) return;
      const finished = drag;
      drag = null;
      if (!finished.moved) return;
      suppressClick = true;
      const node = (graph.nodes || []).find((item) => item.id === finished.nodeId);
      if (!node) return;
      const next = Object.assign({}, node, { position: { x: Math.round(positions[node.id].x), y: Math.round(positions[node.id].y) } });
      try {
        const saved = await api('POST', '/projects/' + encodeURIComponent(S.book) + '/story-graph/delta', { expectedRev: graph.revision, delta: { nodes: { upsert: [next] } } });
        graph = saved.graph;
        positions = filmFlowPositions(graph.nodes);
        toast('Node position saved', 'success');
        renderFlow();
      } catch (error) { toast(error.message, 'error'); renderFlow(); }
    });
    renderFlow();
  };
  window.saveFilmNode = async function (nodeId) {
    const graph = window.__filmGraph;
    const node = graph?.nodes?.find((item) => item.id === nodeId);
    if (!node) return;
    let choices;
    choices = parseFilmChoices(document.getElementById('film-choices-' + nodeId)?.value || '', node.choices || []);
    const next = Object.assign({}, node, {
      title: document.getElementById('film-title-' + nodeId)?.value || '',
      type: document.getElementById('film-type-' + nodeId)?.value || node.type,
      sceneDesc: document.getElementById('film-scene-' + nodeId)?.value || '',
      choices
    });
    try {
      await api('POST', '/projects/' + encodeURIComponent(S.book) + '/story-graph/delta', { expectedRev: graph.revision, delta: { nodes: { upsert: [next] } } });
      toast('互动影像节点已保存', 'success');
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };
  window.addFilmNode = async function () {
    const graph = window.__filmGraph;
    if (!graph) return;
    const node = { id: 'node_' + Date.now().toString(36), title: '新节点', type: 'normal', sceneDesc: '', dialogue: [], choices: [] };
    try {
      await api('POST', '/projects/' + encodeURIComponent(S.book) + '/story-graph/delta', { expectedRev: graph.revision, delta: { nodes: { upsert: [node] } } });
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };
  window.generateFilmGraph = async function () {
    const brief = document.getElementById('film-brief')?.value.trim();
    if (!brief) return toast('请先填写互动影像设定', 'warning');
    const target = document.getElementById('film-task');
    try {
      const queued = await api('POST', '/projects/' + encodeURIComponent(S.book) + '/story-graph/generate', { title: bookName(), brief });
      pollEnhancedTask(queued.taskId, target, '互动影像图谱生成', async (task) => {
        if (task.status === 'completed') { toast('互动影像图谱已保存', 'success'); await render(); }
      });
    } catch (error) { if (target) target.innerHTML = '<div class="warn-banner">' + esc(error.message) + '</div>'; }
  };
  window.generateFilmNodeImage = async function (nodeId) {
    const graph = window.__filmGraph;
    const node = graph?.nodes?.find((item) => item.id === nodeId);
    if (!node) return;
    try {
      const queued = await api('POST', '/projects/' + encodeURIComponent(S.book) + '/nodes/' + encodeURIComponent(nodeId) + '/image', { prompt: node.imageSlot?.prompt || node.sceneDesc || '', size: '1024x1024' });
      const target = document.getElementById('film-task');
      pollEnhancedTask(queued.taskId, target, '节点配图', async (task) => { if (task.status === 'completed') { toast('节点图片已保存', 'success'); await render(); } });
    } catch (error) { toast(error.message, 'error'); }
  };
  window.openInteractiveFilm = function (projectId) {
    setActiveBook(projectId);
    renderNav();
    go('film');
  };
  window.createInteractiveFilm = async function () {
    const title = document.getElementById('new-film-title')?.value.trim();
    const brief = document.getElementById('new-film-brief')?.value.trim() || '';
    const bookId = document.getElementById('new-film-book')?.value || '';
    if (!title) return toast('互动影像标题不能为空', 'warning');
    try {
      const result = await api('POST', '/interactive-films', { title, brief, bookId });
      openInteractiveFilm(result.projectId);
      if (result.taskId) {
        const target = document.getElementById('film-task');
        pollEnhancedTask(result.taskId, target, '互动影像图谱生成', async (task) => { if (task.status === 'completed') { toast('互动影像图谱已生成', 'success'); await render(); } });
      }
    } catch (error) { toast(error.message, 'error'); }
  };
  PAGES.films = async (p) => {
    const data = await api('GET', '/interactive-films');
    await ensureBookList();
    const options = S.books.map((book) => '<option value="' + escAttr(book.id) + '"' + (book.id === S.book ? ' selected' : '') + '>' + esc(book.title) + '</option>').join('');
    const filmCards = (data.films || []).map((film) => '<div class="card card-hover" onclick="openInteractiveFilm(&quot;' + escAttr(film.projectId) + '&quot;)"><div class="card-title-row"><h3>' + esc(film.title || film.projectId) + '</h3><span class="badge badge-info">' + esc(film.nodeCount) + ' nodes</span></div><p class="dim-note">revision ' + esc(film.revision) + '</p><button class="btn btn-sm btn-secondary" onclick="event.stopPropagation();openInteractiveFilm(&quot;' + escAttr(film.projectId) + '&quot;)">打开工作台</button></div>').join('') || '<div class="card"><p class="dim-note">暂无互动影像项目。先填写上方简报创建一个项目。</p></div>';
    p.innerHTML = header('互动影像项目', '对应 InkOS 的互动影游入口、图谱工作台和 StoryPlayer', '<button class="btn btn-primary" onclick="createInteractiveFilm()">创建互动影像</button>') +
      '<div class="content"><div class="card"><div class="grid grid-3"><label class="fld">标题<input class="input" id="new-film-title" placeholder="例如：雾中车站"></label>' +
      '<label class="fld">绑定作品<select class="input" id="new-film-book"><option value="">新建作品</option>' + options + '</select></label>' +
      '<label class="fld">设定简报<input class="input" id="new-film-brief" placeholder="世界、冲突、分支与结局"></label></div><p class="dim-note">填写简报后会创建持久任务调用真实模型；没有模型配置时任务会明确失败，不会生成假图谱。</p></div>' +
      '<div class="grid grid-2">' + filmCards + '</div></div>';
  };
  PAGES.film = async (p) => {
    if (!S.book) return go('films');
    if (!await hasInteractiveFilm(S.book)) {
      renderFeatureEmpty(p, '互动影像工作台', '当前作品还没有互动影像图谱。先创建一个互动影像项目，或在 Studio 扩展中打开已有项目。', '<button class="btn btn-primary" onclick="go(\'films\')">创建互动影像</button>');
      return;
    }
    let graph, validation, analysis;
    try {
      [graph, validation, analysis] = await Promise.all([
        api('GET', '/projects/' + encodeURIComponent(S.book) + '/story-graph'),
        api('GET', '/projects/' + encodeURIComponent(S.book) + '/story-graph/validation'),
        api('GET', '/projects/' + encodeURIComponent(S.book) + '/story-graph/analysis')
      ]);
    } catch (error) {
      if (isMissingFeatureError(error)) {
        renderFeatureEmpty(p, '互动影像工作台', '当前作品还没有互动影像图谱。先创建一个互动影像项目，或在 Studio 扩展中打开已有项目。', '<button class="btn btn-primary" onclick="go(\'films\')">创建互动影像</button>');
        return;
      }
      throw error;
    }
    window.__filmGraph = graph;
    const issueHtml = (validation.issues || []).map((issue) => '<div class="list-row"><span class="badge ' + (issue.level === 'error' ? 'badge-error' : issue.level === 'warning' ? 'badge-warning' : 'badge-muted') + '">' + esc(issue.code) + '</span><span>' + esc(issue.message) + '</span></div>').join('') || '<p class="dim-note">没有结构问题。</p>';
    p.innerHTML = header('互动影像工作台', esc(graph.title || bookName()) + ' · revision ' + esc(graph.revision), '<button class="btn btn-primary" onclick="go(\'story-player\')">试玩 StoryPlayer</button><button class="btn btn-secondary" onclick="addFilmNode()">新增节点</button><button class="btn btn-secondary" onclick="go(\'cover\')">封面</button>') +
      '<div class="content"><div class="warn-banner">图谱是持久化创作资产。保存节点会校验 revision；校验失败时仍保留作者编辑，不会静默覆盖其他版本。</div>' +
      '<div class="grid grid-3"><div class="card"><h3>世界锚点</h3><pre class="mono" style="white-space:pre-wrap">' + pretty(graph.worldAnchor || {}) + '</pre></div><div class="card"><h3>路径统计</h3><div class="kv"><span>节点</span><b>' + esc(analysis.distribution?.nodeCount) + '</b></div><div class="kv"><span>选项边</span><b>' + esc(analysis.distribution?.edgeCount) + '</b></div><div class="kv"><span>结局</span><b>' + esc(analysis.distribution?.endingCount) + '</b></div><div class="kv"><span>平均选项</span><b>' + esc(analysis.distribution?.averageChoices) + '</b></div></div><div class="card"><h3>导出</h3><div class="row row-wrap"><button class="btn btn-sm btn-secondary" onclick="downloadStudioRoute(\'/projects/' + encodeURIComponent(S.book) + '/export/json\',\'' + escAttr(S.book) + '.story-graph.json\')">JSON</button><button class="btn btn-sm btn-secondary" onclick="downloadStudioRoute(\'/projects/' + encodeURIComponent(S.book) + '/export/ink\',\'' + escAttr(S.book) + '.ink\')">Ink</button><button class="btn btn-sm btn-secondary" onclick="downloadStudioRoute(\'/projects/' + encodeURIComponent(S.book) + '/export/html\',\'' + escAttr(S.book) + '.html\')">HTML</button><button class="btn btn-sm btn-secondary" onclick="downloadStudioRoute(\'/projects/' + encodeURIComponent(S.book) + '/export\',\'' + escAttr(S.book) + '.tar.gz\')">Package</button></div></div></div>' +
      '<div class="card"><div class="card-title-row"><h3>模型生成/重生成</h3><button class="btn btn-primary" onclick="generateFilmGraph()">生成图谱</button></div><textarea class="input textarea" id="film-brief" placeholder="描述世界锚点、角色、分支条件、至少两个不同结局"></textarea><div id="film-task"></div></div>' +
      '<div class="grid grid-2"><div><h3 class="mb8">节点编辑</h3>' + (graph.nodes || []).map(filmNodeEditor).join('') + '</div><div><div class="card"><div class="card-title-row"><h3>图谱校验</h3><span class="badge ' + (validation.ok ? 'badge-success' : 'badge-error') + '">' + (validation.ok ? '可试玩' : '需修复') + '</span></div>' + issueHtml + '</div><div class="card"><h3>情绪/结构分组</h3>' + (analysis.arcs || []).map((arc) => '<div class="list-row"><span>' + esc(arc.act) + '</span><span class="spacer"></span><span class="text-sm text-muted">' + esc(arc.nodes) + ' nodes · ' + esc(arc.choices) + ' choices</span></div>').join('') + '</div></div></div></div>';
    const filmContent = p.querySelector('.content');
    if (filmContent) {
      filmContent.insertAdjacentHTML('afterbegin', filmPhaseBar(graph, validation) + filmPhasePanel(graph, validation, analysis));
      const worldCard = filmContent.querySelector('.grid.grid-3 > .card');
      if (worldCard) worldCard.innerHTML = '<h3>世界锚点</h3>' + readableProjection(graph.worldAnchor || {});
      [...filmContent.querySelectorAll('button')].filter((button) => button.textContent.trim() === 'JSON').forEach((button) => button.remove());
    }
  };

  // ========== Durable StoryPlayer ==========
  const playerKey = () => 'novelforge-story-player-' + (S.book || 'global');
  async function loadPlayerState() {
    let sessionId = localStorage.getItem(playerKey()) || '';
    if (!sessionId) {
      const started = await api('POST', '/projects/' + encodeURIComponent(S.book) + '/play/start');
      sessionId = started.session.sessionId;
      localStorage.setItem(playerKey(), sessionId);
      return started;
    }
    try {
      return await api('GET', '/projects/' + encodeURIComponent(S.book) + '/play/sessions/' + encodeURIComponent(sessionId));
    } catch (_) {
      const started = await api('POST', '/projects/' + encodeURIComponent(S.book) + '/play/start');
      localStorage.setItem(playerKey(), started.session.sessionId);
      return started;
    }
  }
  window.restartStoryPlayer = async function () {
    try {
      const started = await api('POST', '/projects/' + encodeURIComponent(S.book) + '/play/start');
      localStorage.setItem(playerKey(), started.session.sessionId);
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };
  window.chooseStoryPlayer = async function (choiceId) {
    const sessionId = localStorage.getItem(playerKey());
    if (!sessionId) return restartStoryPlayer();
    try {
      const state = await api('POST', '/projects/' + encodeURIComponent(S.book) + '/play/sessions/' + encodeURIComponent(sessionId) + '/choose', { choiceId });
      window.__playerState = state;
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };
  PAGES['story-player'] = async (p) => {
    if (!S.book) return go('films');
    if (!await hasInteractiveFilm(S.book)) {
      renderFeatureEmpty(p, 'StoryPlayer', '当前作品还没有可试玩的互动影像图谱。先创建并校验图谱，再从这里开始试玩。', '<button class="btn btn-primary" onclick="go(\'films\')">创建互动影像</button>');
      return;
    }
    let state;
    try {
      state = await loadPlayerState();
    } catch (error) {
      if (isMissingFeatureError(error)) {
        renderFeatureEmpty(p, 'StoryPlayer', '当前作品还没有可试玩的互动影像图谱。先创建并校验图谱，再从这里开始试玩。', '<button class="btn btn-primary" onclick="go(\'films\')">创建互动影像</button>');
        return;
      }
      throw error;
    }
    window.__playerState = state;
    const node = state.node || {};
    const ending = state.ending;
    const choices = state.choices || [];
    const image = node.imageSlot?.assetRef ? '<img src="' + filmAssetUrl(node.imageSlot.assetRef) + '" alt="" style="width:100%;max-height:360px;object-fit:cover;border-radius:12px;margin-bottom:16px">' : '';
    const dialogue = (node.dialogue || []).map((line) => '<div class="list-row"><span class="badge badge-info">' + esc(line.speaker) + '</span><span>' + esc(line.text) + '</span></div>').join('');
    const choiceHtml = choices.map((choice) => '<button class="btn btn-secondary" style="width:100%;justify-content:flex-start;margin-top:8px;padding:14px" onclick="chooseStoryPlayer(\'' + escAttr(choice.id) + '\')">' + esc(choice.text || choice.id) + '</button>').join('');
    const endingHtml = ending || node.type === 'ending' ? '<div class="card" style="border-color:var(--success);text-align:center"><span class="badge badge-success">' + esc(ending?.type || 'ending') + '</span><h2 style="margin:12px 0">' + esc(ending?.title || node.title || '结局') + '</h2><p class="dim-note">' + esc(ending?.description || '') + '</p><button class="btn btn-primary mt16" onclick="restartStoryPlayer()">重新开始</button></div>' : (choices.length ? '<div class="card"><h3>选择行动</h3>' + choiceHtml + '</div>' : '<div class="warn-banner">当前节点没有可用选项；请回到工作台修复图谱。</div>');
    p.innerHTML = header('StoryPlayer', esc(bookName()) + ' · 持久化会话', '<button class="btn btn-secondary" onclick="go(\'film\')">返回工作台</button><button class="btn btn-secondary" onclick="restartStoryPlayer()">重置</button>') +
      '<div class="content"><div class="grid grid-3"><div class="card" style="grid-column:span 2"><h2>' + esc(node.title || node.id || '未开始') + '</h2><p class="dim-note mt8">' + esc(node.sceneDesc || '') + '</p>' + image + dialogue + endingHtml + '</div><div><div class="card"><h3>状态 HUD</h3>' + Object.entries(state.session?.variables || {}).map(([key, value]) => '<div class="kv"><span>' + esc(key) + '</span><b>' + esc(value) + '</b></div>').join('') + '<div class="kv"><span>已解锁结局</span><b>' + esc((state.session?.unlockedEndings || []).length) + ' / ' + esc(state.endingCount || 0) + '</b></div></div>' + (state.stale ? '<div class="warn-banner">图谱已被作者修改，请重置播放器后继续。</div>' : '') + '</div></div></div>';
  };

  // ========== Cover generation ==========
  window.generateBookCover = async function () {
    const target = document.getElementById('cover-task');
    try {
      const queued = await api('POST', '/books/' + encodeURIComponent(S.book) + '/cover/generate', { prompt: document.getElementById('cover-prompt')?.value || '', size: document.getElementById('cover-size')?.value || '1024x1024', quality: document.getElementById('cover-quality')?.value || '', style: document.getElementById('cover-style')?.value || '' });
      pollEnhancedTask(queued.taskId, target, '封面图片生成', async (task) => { if (task.status === 'completed') { toast('封面已生成', 'success'); await render(); } });
    } catch (error) { if (target) target.innerHTML = '<div class="warn-banner">' + esc(error.message) + '</div>'; }
  };
  PAGES.cover = async (p) => {
    if (!S.book) return go('dashboard');
    const cover = await api('GET', '/books/' + encodeURIComponent(S.book) + '/cover');
    p.innerHTML = header('封面生成', '调用已配置的 image Provider 生成真实图片资产；未配置时任务会明确失败', '<button class="btn btn-primary" onclick="generateBookCover()">生成封面</button>') +
      '<div class="content"><div class="warn-banner">提示词会持久化到任务和封面 manifest。当前页面不会把文本简报冒充成图片。</div><div class="grid grid-2"><div class="card"><label class="fld">封面提示词<textarea class="input textarea" id="cover-prompt" placeholder="书名气质、主体、构图、光线、禁用文字…"></textarea></label><div class="grid grid-3"><label class="fld">尺寸<select class="input" id="cover-size"><option>1024x1024</option><option>1536x1024</option><option>1024x1536</option></select></label><label class="fld">质量<select class="input" id="cover-quality"><option value="">默认</option><option>standard</option><option>hd</option></select></label><label class="fld">风格<select class="input" id="cover-style"><option value="">默认</option><option>vivid</option><option>natural</option></select></label></div><div id="cover-task"></div></div><div class="card"><h3>当前封面</h3>' + (cover.available ? '<img src="/api/v1/books/' + encodeURIComponent(S.book) + '/cover/file?ts=' + Date.now() + '" alt="" style="width:100%;max-height:520px;object-fit:contain;border-radius:10px"><p class="dim-note mt8">模型：' + esc(cover.model || '') + ' · ' + esc(cover.generatedAt || '') + '</p>' : '<p class="dim-note">尚未生成封面。</p>') + '</div></div></div>';
  };

  // Live task events refresh durable pages. The event carries no authoritative
  // result; each page re-reads its own API state before rendering.
  let refreshTimer = null;
  window.addEventListener('task-update', () => {
    if (!['dashboard', 'tasks'].includes(S.page)) return;
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(() => render(), 300);
  });

  // Visualization entries are compatibility aliases for the unified
  // StoryFlow projection. Keep the old renderers addressable for deep links
  // and local recovery, but make normal navigation answer the same graph
  // questions through one Canvas controller.
  const legacyStoryFlowPages = {};
  const storyFlowAliases = {
    mindmap: 'story',
    flow: 'story',
    timeline: 'timeline',
    plot: 'story',
    'world-map': 'world',
    foreshadowing: 'foreshadow',
    characters: 'character',
  };
  Object.entries(storyFlowAliases).forEach(([pageName, view]) => {
    const legacyPage = PAGES[pageName];
    if (typeof legacyPage !== 'function') return;
    legacyStoryFlowPages[pageName] = legacyPage;
    PAGES[`legacy-${pageName}`] = legacyPage;
    PAGES[pageName] = async (page) => {
      if (typeof window.openStoryFlowView === 'function') {
        window.openStoryFlowView(view);
        return;
      }
      // The base page can render once before the lazy StoryFlow asset arrives.
      // Preserve a truthful fallback instead of leaving a blank page.
      await legacyPage(page);
    };
  });
  window.renderLegacyStoryFlowPage = async function (pageName, page = document.getElementById('page')) {
    const legacyPage = legacyStoryFlowPages[pageName];
    if (!legacyPage || !page) throw new Error(`legacy visualization page not found: ${pageName}`);
    await legacyPage(page);
  };

  // ========== User-managed Agent extensions ==========
  const studioExtensionCatalog = [
    ['style', '文风管理', '分析参考文本并把结构化文风导入当前作品。', '可用', '作品'],
    ['modes', '创作模式', '把短篇、剧本、分镜、互动和衍生模式接入 AI 助手。', '可用', '全局 / 作品'],
    ['translation', '翻译工作台', '上传文本、持久化分段翻译任务并导出结果。', '可用', '全局'],
    ['radar', '题材雷达', '排队执行题材研究并保存扫描历史；不冒充实时市场数据。', '可用', '全局'],
    ['prompts', '提示词注册表', '管理任务类型的提示词版本；作品内可覆盖全局默认。', '可用', '全局 / 作品'],
    ['backups', '备份与恢复', '创建、校验和恢复数据库快照。', '可用', '全局 / 作品'],
    ['daemon', '守护进程', '查看或控制持久任务 Worker；环境禁用时不能启动。', '运行控制', '全局'],
    ['logs', '运行日志', '查看持久化任务与运行日志，定位失败原因。', '可用', '全局'],
    ['films', '互动影像', '管理互动影像项目；具体工作台在打开作品后进入。', '可用', '全局 / 作品'],
  ];
  PAGES['extension-home'] = async (p) => {
    const cards = studioExtensionCatalog.map(([id, title, description, status, scope]) => '<div class="card"><div class="card-title-row"><h3>' + esc(title) + '</h3><span class="badge ' + (status === '可用' || status === '可配置' ? 'badge-success' : 'badge-info') + '">' + esc(status) + '</span></div><p class="dim-note">' + esc(description) + '</p><div class="row mt16"><span class="badge badge-muted">' + esc(scope) + '</span><span class="spacer"></span><button class="btn btn-sm btn-secondary" onclick="go(\'' + escAttr(id) + '\')">打开</button></div></div>').join('');
    p.innerHTML = header('Studio 扩展', '按“用途、作用范围、当前能力”整理入口；看到“可用”即可直接操作，MCP 的未接入部分会在页面明确说明。', '') + '<div class="content"><div class="warn-banner">全局 AI 配置只需要设置一次。选中某部作品后，在“作品设置”里可以单独启用、停用或恢复 Skill / MCP 的全局默认。</div><div class="grid grid-3">' + cards + '</div></div>';
  };
  const extensionState = { config: null, skills: [], mcpServers: [] };
  const runtimeInstallationAction = (runtime) => {
    const state = runtime?.installation?.state || '';
    return state === 'not_installed' ? 'install' : 'reconnect';
  };
  const runtimeOnboardingCard = (registryState, capabilityState) => {
    const runtimes = Array.isArray(registryState?.runtimes) ? registryState.runtimes : [];
    const capabilities = new Map(
      (Array.isArray(capabilityState?.runtimes) ? capabilityState.runtimes : [])
        .map((item) => [item.runtimeType, item]),
    );
    const ready = runtimes.some((item) => {
      const runtimeType = item?.manifest?.runtimeType;
      return item?.installation?.state === 'ready' || capabilities.get(runtimeType)?.health === 'ready';
    });
    if (ready) return '';
    const byType = new Map(runtimes.map((item) => [item?.manifest?.runtimeType, item]));
    const status = (runtimeType) => byType.get(runtimeType)?.installation?.state || 'not observed';
    const codex = byType.get('codex-app-server');
    const codexAction = runtimeInstallationAction(codex);
    const statusText = (runtimeType) => esc(status(runtimeType));
    return '<div class="card runtime-onboarding" data-runtime-onboarding="true">' +
      '<div class="card-title-row"><div><div class="wizard-kicker">Welcome to NovelForge</div><h2>Choose Intelligence Runtime</h2><p class="dim-note">NovelForge 保留作品、任务和 Canon 的控制权；Runtime 只是可替换的智能执行器。先选择一个可用入口，之后仍可在高级设置中切换。</p></div><span class="badge badge-info">首次使用</span></div>' +
      '<div class="card runtime-onboarding-recommended"><div class="card-title-row"><div><span class="badge badge-success">Recommended</span><h3 class="mt8">Codex</h3><p class="dim-note">Use your ChatGPT account · No API key required</p><p class="text-sm mt8">通过官方 App Server 认证；当前状态：' + statusText('codex-app-server') + '</p></div><button class="btn btn-primary" onclick="runtimeOnboardingAction(\'codex-app-server\',\'' + codexAction + '\')">Continue with ChatGPT</button></div></div>' +
      '<div class="grid grid-2 mt16"><div><h3>Other runtimes</h3><div class="row row-wrap mt8">' +
        '<button class="btn btn-secondary" onclick="runtimeOnboardingAction(\'claude-code\',\'' + runtimeInstallationAction(byType.get('claude-code')) + '\')">Claude Code · ' + statusText('claude-code') + '</button>' +
        '<button class="btn btn-secondary" onclick="runtimeOnboardingAction(\'gemini-cli\',\'' + runtimeInstallationAction(byType.get('gemini-cli')) + '\')">Gemini CLI · ' + statusText('gemini-cli') + '</button>' +
        '<button class="btn btn-secondary" onclick="runtimeOnboardingAction(\'local-runtime\',\'' + runtimeInstallationAction(byType.get('local-runtime')) + '\')">Local · ' + statusText('local-runtime') + '</button>' +
        '<button class="btn btn-secondary" onclick="runtimeOpenApiSetup()">OpenAI API · 配置</button>' +
      '</div></div><div><h3>下一步</h3><ol class="provider-guide-steps mt8"><li>选择 Runtime 或配置 API Provider。</li><li>完成官方认证 / 连接检查。</li><li>看到 Ready 后开始创作。</li></ol><p class="dim-note">安装、认证和能力探测由 Runtime Plane 负责；未 Ready 的 Runtime 不会被 Scheduler 静默使用。</p></div></div>' +
      '</div>';
  };
  const runtimeDashboardSetupCard = (registryState) => {
    const runtimes = Array.isArray(registryState?.runtimes) ? registryState.runtimes : [];
    if (runtimes.some((item) => item?.installation?.state === 'ready')) return '';
    const observed = runtimes
      .map((item) => item?.manifest?.displayName || item?.manifest?.runtimeType)
      .filter(Boolean)
      .slice(0, 4)
      .join(' · ');
    return '<div class="card runtime-first-use" data-runtime-first-use="true"><div class="card-title-row"><div><div class="wizard-kicker">Welcome to NovelForge</div><h2>先选择你的 Intelligence Runtime</h2><p class="dim-note">选择 Codex、API、Claude、Gemini 或 Local 后再开始创作。作品和 Canon 始终由 NovelForge 管理，Runtime 未 Ready 时不会被当作可用引擎。</p>' +
      (observed ? '<p class="text-sm mt8">已发现：' + esc(observed) + '</p>' : '') +
      '</div><button class="btn btn-primary" onclick="go(\'agent-config\')">选择 AI Runtime</button></div></div>';
  };
  const legacyDashboardPage = PAGES.dashboard;
  PAGES.dashboard = async (p) => {
    await legacyDashboardPage(p);
    try {
      const registryState = await api('GET', '/runtime/registry');
      const markup = runtimeDashboardSetupCard(registryState);
      const content = p.querySelector('.content');
      if (!markup || !content || content.querySelector('[data-runtime-first-use]')) return;
      const wrapper = document.createElement('div');
      wrapper.innerHTML = markup;
      if (wrapper.firstElementChild) content.prepend(wrapper.firstElementChild);
    } catch (_) {
      // Runtime setup is an additive dashboard hint; the health/error page is
      // still rendered by the base Dashboard when the registry is unavailable.
    }
  };
  // The provider table uses a unique display name.  Keep preset-based setup
  // convenient when the author adds two accounts from the same vendor.
  const originalAddProviderPreset = window.addProviderPreset;
  if (typeof originalAddProviderPreset === 'function') {
    window.addProviderPreset = function () {
      const presetId = document.getElementById('provider-preset')?.value;
      const presets = window.__providerPresets || [];
      const preset = presets.find((item) => item.id === presetId);
      if (!preset) return originalAddProviderPreset();
      const usedNames = new Set([...document.querySelectorAll('.provider-model-card .pm-name')].map((item) => item.value.trim()));
      let alias = preset.name;
      let suffix = 2;
      while (usedNames.has(alias)) alias = preset.name + ' ' + suffix++;
      if (alias === preset.name) return originalAddProviderPreset();
      window.__providerPresets = presets.map((item) => item.id === presetId ? { ...item, name: alias } : item);
      try { return originalAddProviderPreset(); }
      finally { window.__providerPresets = presets; }
    };
  }
  const extensionJson = (value, label) => {
    const text = value.trim();
    if (!text) return {};
    try { return JSON.parse(text); } catch (_) {
      const result = {};
      for (const line of text.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)) {
        const separator = line.indexOf('=') >= 0 ? line.indexOf('=') : line.indexOf('：');
        if (separator <= 0) throw new Error(label + ' 的填写格式无法识别，请使用“名称=内容”逐行填写');
        const key = line.slice(0, separator).trim();
        const item = line.slice(separator + 1).trim();
        if (key) result[key] = item;
      }
      return result;
    }
  };
  const extensionObjectText = (value) => Object.entries(value || {}).map(([key, item]) => `${key}=${typeof item === 'string' ? item : taskValueText(item)}`).join('\n');
  const extensionValue = (id) => document.getElementById(id)?.value || '';
  const extensionChecked = (id) => Boolean(document.getElementById(id)?.checked);
  const extensionModelOptions = (models, selected) => '<option value="">未配置</option>' + (models || []).map((item) =>
    '<option value="' + escAttr(item.id) + '"' + (item.id === selected ? ' selected' : '') + '>' +
    esc(item.name || item.modelId || item.id) + ' (' + esc(item.modelId || '') + ')</option>'
  ).join('');

  async function loadExtensionState() {
    const [config, skills, mcp] = await Promise.all([
      api('GET', '/services/config'),
      api('GET', '/skills'),
      api('GET', '/mcp-servers'),
    ]);
    extensionState.config = config;
    extensionState.skills = skills.skills || [];
    extensionState.mcpServers = mcp.mcpServers || [];
    return extensionState;
  }

  function collectProviderModelConfig() {
    const cards = [...document.querySelectorAll('.provider-model-card')];
    const providerMap = new Map();
    const models = [];
    for (const card of cards) {
      const provider = {
        id: card.dataset.providerId,
        name: card.querySelector('.pm-name')?.value.trim() || card.dataset.providerId,
        providerType: card.querySelector('.pm-type')?.value || 'custom',
        baseUrl: card.querySelector('.pm-url')?.value.trim() || '',
      };
      const key = card.querySelector('.pm-key')?.value || '';
      const env = card.querySelector('.pm-env')?.value.trim() || '';
      if (key) provider.apiKey = key;
      else if (env) provider.credentialEnv = env;
      providerMap.set(provider.id, { ...(providerMap.get(provider.id) || {}), ...provider });
      const modelId = card.querySelector('.pm-model-id')?.value.trim() || '';
      if (modelId) models.push({
        id: card.dataset.modelId,
        providerId: provider.id,
        name: card.querySelector('.pm-model-name')?.value.trim() || modelId,
        modelId,
      });
    }
    const routes = {};
    document.querySelectorAll('select.route-model[data-role]').forEach((element) => {
      if (element.value) routes[element.dataset.role] = element.value;
    });
    const routePromptOverrides = {};
    document.querySelectorAll('#agent-route-list textarea[data-role]').forEach((element) => {
      routePromptOverrides[element.dataset.role] = element.value;
    });
    return { providers: [...providerMap.values()], models, routes, routePromptOverrides };
  }
  window.collectProviderModelConfig = collectProviderModelConfig;
  window.refreshRouteOptions = function () {
    const current = [...document.querySelectorAll('.route-model')].reduce((result, element) => {
      result[element.dataset.role] = element.value;
      return result;
    }, {});
    const models = [...document.querySelectorAll('.provider-model-card')].map((card) => ({
      id: card.dataset.modelId,
      name: card.querySelector('.pm-model-name')?.value || 'Unnamed model',
      modelId: card.querySelector('.pm-model-id')?.value || '',
    }));
    const target = document.getElementById('agent-route-list') || document.getElementById('route-list');
    if (target?.querySelector('textarea[data-role]')) {
      target.querySelectorAll('select.route-model[data-role]').forEach((element) => {
        const selected = element.value;
        element.innerHTML = extensionModelOptions(models, selected);
      });
      return;
    }
    if (target) target.innerHTML = Object.keys(MODEL_ROLES).map((role) => routeSelector(role, models, current[role])).join('');
  };
  window.addProviderModel = function () {
    const list = document.getElementById('provider-model-list');
    if (!list) return;
    list.insertAdjacentHTML('beforeend', providerModelCard({ id: newModelId('provider'), providerType: 'custom' }, {}));
    window.refreshRouteOptions();
  };
  window.addModelForProvider = function (providerId) {
    const source = [...document.querySelectorAll('.provider-model-card')].find((card) => card.dataset.providerId === providerId);
    const list = document.getElementById('provider-model-list');
    if (!source || !list) return;
    const provider = {
      id: providerId,
      name: source.querySelector('.pm-name')?.value.trim() || providerId,
      providerType: source.querySelector('.pm-type')?.value || 'custom',
      baseUrl: source.querySelector('.pm-url')?.value.trim() || '',
      credentialEnv: source.querySelector('.pm-env')?.value.trim() || '',
    };
    list.insertAdjacentHTML('beforeend', providerModelCard(provider, { id: newModelId('model') }));
    window.refreshRouteOptions();
    toast('已添加模型编辑卡片，填写模型标识后保存即可', 'info');
  };
  window.addProviderPreset = function () {
    const presetId = document.getElementById('provider-preset')?.value;
    const preset = (window.__providerPresets || []).find((item) => item.id === presetId);
    const list = document.getElementById('provider-model-list');
    if (!preset || !list) return;
    const provider = { id: newModelId('provider'), name: preset.name, providerType: preset.providerType, baseUrl: preset.baseUrl };
    const model = { id: newModelId('model'), name: preset.modelName, modelId: preset.modelId };
    list.insertAdjacentHTML('beforeend', providerModelCard(provider, model, preset));
    document.getElementById('provider-preset').value = '';
    window.refreshRouteOptions();
  };
  window.saveServices = async function () {
    const collected = collectProviderModelConfig();
    if (!collected.providers.length) return toast('请先添加一个供应商。模型可以稍后再添加。', 'warning');
    try {
      await api('PUT', '/services/config', collected);
      toast(collected.models.length ? '供应商、模型与 Agent 配置已保存' : '供应商已保存，可以继续添加模型', 'success');
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };

  window.deleteModelFromEditor = async function (providerId, modelId) {
    const card = [...document.querySelectorAll('.provider-model-card')]
      .find((item) => item.dataset.providerId === providerId && item.dataset.modelId === modelId);
    const isSaved = window.__savedModelIds?.has(modelId);
    if (!isSaved) {
      card?.remove();
      window.refreshRouteOptions();
      toast('模型已从当前编辑区移除', 'success');
      return;
    }
    if (!window.confirm('确认删除这个模型？已分配给它的 Agent 路由也会一并解除。')) return;
    try {
      await api('DELETE', '/services/models/' + encodeURIComponent(modelId));
      toast('模型已删除', 'success');
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };

  window.deleteProviderFromEditor = async function (providerId) {
    const isSaved = window.__savedProviderIds?.has(providerId);
    if (!isSaved) {
      document.querySelectorAll('.provider-model-card').forEach((card) => {
        if (card.dataset.providerId === providerId) card.remove();
      });
      if (!document.querySelector('.provider-model-card')) window.addProviderModel();
      window.refreshRouteOptions();
      toast('供应商已从当前编辑区移除', 'success');
      return;
    }
    if (!window.confirm('确认删除这个供应商？它下面的全部模型和路由都会被删除。')) return;
    try {
      await api('DELETE', '/services/providers/' + encodeURIComponent(providerId));
      toast('供应商及其模型已删除', 'success');
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };

  window.saveAgentRoutes = async function () {
    const collected = collectProviderModelConfig();
    if (!collected.providers.length && !collected.models.length) return toast('请先配置至少一个 Provider / Model', 'warning');
    try {
      await api('PUT', '/services/config', collected);
      toast('Agent 路由与补充规则已保存', 'success');
      await render();
    } catch (error) { toast(error.message, 'error'); }
    return;
    const config = extensionState.config || {};
    const routes = {};
    const systemPrompts = {};
    document.querySelectorAll('#agent-route-list [data-role]').forEach((element) => {
      const role = element.dataset.role;
      if (element.value) routes[role] = element.value;
    });
    document.querySelectorAll('#agent-route-list textarea[data-role]').forEach((element) => {
      systemPrompts[element.dataset.role] = element.value;
    });
    try {
      await api('PUT', '/services/config', {
        providers: config.providers || [],
        models: config.models || [],
        routes,
        systemPrompts,
      });
      toast('Agent 路由与系统提示词已保存', 'success');
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };

  window.resetAgentPrompt = function (role) {
    const element = document.querySelector('#agent-route-list textarea[data-role="' + CSS.escape(role) + '"]');
    if (element) { element.value = ''; return; }
    const fallback = (extensionState.config?.defaultRoutePrompts || {})[role] || '';
    if (element) element.value = fallback;
  };

  window.resetSkillForm = function () {
    ['skill-edit-id', 'skill-name', 'skill-key', 'skill-description', 'skill-instructions', 'skill-config'].forEach((id) => {
      const element = document.getElementById(id);
      if (element) element.value = '';
    });
    const enabled = document.getElementById('skill-enabled');
    if (enabled) enabled.checked = true;
  };
  window.editSkill = function (skillId) {
    const skill = extensionState.skills.find((item) => item.id === skillId);
    if (!skill) return;
    document.getElementById('skill-edit-id').value = skill.id || '';
    document.getElementById('skill-name').value = skill.name || '';
    document.getElementById('skill-key').value = skill.key || '';
    document.getElementById('skill-description').value = skill.description || '';
    document.getElementById('skill-instructions').value = skill.instructions || '';
    document.getElementById('skill-config').value = extensionObjectText(skill.config || {});
    document.getElementById('skill-enabled').checked = Boolean(skill.enabled);
    document.getElementById('skill-name')?.focus();
  };
  window.saveSkill = async function () {
    try {
      const id = extensionValue('skill-edit-id').trim();
      const payload = {
        name: extensionValue('skill-name').trim(),
        key: extensionValue('skill-key').trim() || undefined,
        description: extensionValue('skill-description'),
        instructions: extensionValue('skill-instructions'),
        config: extensionJson(extensionValue('skill-config'), 'Skill 配置'),
        enabled: extensionChecked('skill-enabled'),
      };
      await api(id ? 'PUT' : 'POST', id ? '/skills/' + encodeURIComponent(id) : '/skills', payload);
      toast('Skill 已保存', 'success');
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };
  const readSkillFolder = async (files) => {
    const entries = [];
    for (const file of files) {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = '';
      for (let index = 0; index < bytes.length; index += 0x8000) binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
      entries.push({ path: file.webkitRelativePath || file.name, dataUrl: 'data:application/octet-stream;base64,' + btoa(binary) });
    }
    return entries;
  };
  const showSkillImportResult = (result) => {
    if (!result) return;
    let target = document.getElementById('skill-import-result');
    if (!target) {
      const anchor = document.getElementById('skill-folder-files')?.closest('.fld');
      if (!anchor) return;
      target = document.createElement('div');
      target.id = 'skill-import-result';
      target.className = 'dim-note mt8';
      target.setAttribute('aria-live', 'polite');
      anchor.appendChild(target);
    }
    const references = result.referenceFiles || [];
    const source = result.source || result.origin || 'imported';
    const version = result.version == null ? '1' : result.version;
    target.innerHTML = '<span class="badge badge-success">导入成功</span> ' +
      '<span>来源：' + esc(source) + ' · 版本：v' + esc(version) +
      ' · 引用文件：' + esc(references.length) + ' · 脚本未执行</span>' +
      (result.manifestPath ? '<span class="dim-note"> · Manifest：' + esc(result.manifestPath) + '</span>' : '');
  };
  window.importSkillFromGithub = async function () {
    const input = document.getElementById('skill-github-url'), url = input?.value.trim();
    if (!url) return toast('请粘贴 GitHub 仓库、Skill 文件夹或 release 链接', 'error');
    try { const result = await api('POST', '/skills/import', { githubUrl: url }); await render(); showSkillImportResult(result); toast('GitHub Skill 已导入', 'success'); }
    catch (error) { toast(error.message, 'error'); }
  };
  window.importSkillPackage = async function () {
    const file = document.getElementById('skill-package-file')?.files?.[0];
    if (!file) return toast('请选择 Skill 文件包、ZIP 或 TAR 包', 'error');
    const form = new FormData(); form.append('file', file, file.name);
    try { const result = await api('POST', '/skills/import', form, true); await render(); showSkillImportResult(result); toast('Skill 包已导入', 'success'); }
    catch (error) { toast(error.message, 'error'); }
  };
  window.importSkillFolder = async function () {
    const files = [...(document.getElementById('skill-folder-files')?.files || [])];
    if (!files.length) return toast('请选择包含 Skill 文件的文件夹', 'error');
    try { const result = await api('POST', '/skills/import', { files: await readSkillFolder(files), origin: 'local-folder' }); await render(); showSkillImportResult(result); toast('Skill 文件夹已导入', 'success'); }
    catch (error) { toast(error.message, 'error'); }
  };
  window.toggleSkill = async function (skillId, enabled) {
    try {
      await api('PUT', '/skills/' + encodeURIComponent(skillId) + '/enabled', { enabled: !enabled });
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };
  window.deleteSkill = async function (skillId) {
    if (!confirm('确认删除这个用户 Skill？')) return;
    try {
      await api('DELETE', '/skills/' + encodeURIComponent(skillId));
      toast('Skill 已删除', 'success');
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };

  window.resetMcpForm = function () {
    ['mcp-edit-id', 'mcp-name', 'mcp-command', 'mcp-url', 'mcp-args', 'mcp-environment', 'mcp-headers'].forEach((id) => {
      const element = document.getElementById(id);
      if (element) element.value = '';
    });
    const transport = document.getElementById('mcp-transport');
    if (transport) transport.value = 'stdio';
    const enabled = document.getElementById('mcp-enabled');
    if (enabled) enabled.checked = true;
  };
  window.editMcpServer = function (serverId) {
    const server = extensionState.mcpServers.find((item) => item.id === serverId);
    if (!server) return;
    document.getElementById('mcp-edit-id').value = server.id || '';
    document.getElementById('mcp-name').value = server.name || '';
    document.getElementById('mcp-transport').value = server.transport || 'stdio';
    document.getElementById('mcp-command').value = server.command || '';
    document.getElementById('mcp-url').value = server.url || '';
    document.getElementById('mcp-args').value = (server.args || []).join('\n');
    document.getElementById('mcp-environment').value = extensionObjectText(server.environment || {});
    document.getElementById('mcp-headers').value = extensionObjectText(server.headers || {});
    document.getElementById('mcp-enabled').checked = Boolean(server.enabled);
    document.getElementById('mcp-name')?.focus();
  };
  window.saveMcpServer = async function () {
    try {
      const id = extensionValue('mcp-edit-id').trim();
      const args = extensionValue('mcp-args').split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
      const payload = {
        name: extensionValue('mcp-name').trim(),
        transport: extensionValue('mcp-transport'),
        command: extensionValue('mcp-command').trim(),
        url: extensionValue('mcp-url').trim(),
        args,
        environment: extensionJson(extensionValue('mcp-environment'), 'MCP 环境变量'),
        headers: extensionJson(extensionValue('mcp-headers'), 'MCP 请求头'),
        enabled: extensionChecked('mcp-enabled'),
      };
      await api(id ? 'PUT' : 'POST', id ? '/mcp-servers/' + encodeURIComponent(id) : '/mcp-servers', payload);
      toast('MCP 配置已保存', 'success');
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };
  window.toggleMcpServer = async function (serverId, enabled) {
    try {
      await api('PUT', '/mcp-servers/' + encodeURIComponent(serverId) + '/enabled', { enabled: !enabled });
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };
  window.validateMcpServer = async function (serverId) {
    const target = document.getElementById('mcp-validation-' + serverId);
    if (target) target.textContent = '正在检查本地配置…';
    try {
      const result = await api('POST', '/mcp-servers/' + encodeURIComponent(serverId) + '/validate');
      if (target) target.textContent = result.valid ? '配置有效；尚未执行真实 MCP 握手' : '配置无效';
    } catch (error) {
      if (target) target.textContent = error.message;
    }
  };
  window.deleteMcpServer = async function (serverId) {
    if (!confirm('确认删除这个 MCP 配置？')) return;
    try {
      await api('DELETE', '/mcp-servers/' + encodeURIComponent(serverId));
      toast('MCP 配置已删除', 'success');
      await render();
    } catch (error) { toast(error.message, 'error'); }
  };

  PAGES['agent-config'] = async (p) => {
    const state = await loadExtensionState();
    const config = state.config || {};
    const roles = config.roles || Object.keys(MODEL_ROLES);
    const providers = (config.providers || []).map((item) =>
      '<span class="badge badge-info">' + esc(item.name || item.id) + '</span>'
    ).join(' ') || '<span class="dim-note">尚未配置 Provider</span>';
    const modelSummary = (config.models || []).map((item) =>
      '<div class="list-row"><span>' + esc(item.name || item.modelId) + '</span><span class="spacer"></span><span class="text-muted">' + esc(item.modelId || '') + '</span></div>'
    ).join('') || '<p class="dim-note">请先打开模型配置添加 Provider / Model。</p>';
    const routeCards = roles.map((role) => {
      const prompt = (config.routePrompts || {})[role] || (config.defaultRoutePrompts || {})[role] || '';
      return '<div class="card"><div class="card-title-row"><h3>' + esc(MODEL_ROLES[role] || role) + '</h3><span class="badge badge-muted">' + esc(role) + '</span></div>' +
        '<label class="fld">模型<select class="input" data-role="' + escAttr(role) + '">' + extensionModelOptions(config.models, (config.routes || {})[role]) + '</select></label>' +
        '<label class="fld">系统职责补充规则<textarea class="input textarea" data-role="' + escAttr(role) + '" rows="14" placeholder="可选：补充本角色需要遵守的创作要求">' + esc(prompt) + '</textarea></label>' +
        '<div class="row row-wrap"><button class="btn btn-sm btn-secondary" onclick="resetAgentPrompt(\'' + escAttr(role) + '\')">恢复系统模板</button><span class="dim-note">版本：' + esc((config.routePromptVersions || {})[role] || 0) + '；模板包含权限、工作流、输出契约与禁止事项。</span></div></div>';
    }).join('');
    const skillRows = state.skills.map((skill) =>
      '<div class="card"><div class="card-title-row"><h3>' + esc(skill.name) + '</h3><span class="badge ' + (skill.enabled ? 'badge-success' : 'badge-muted') + '">' + (skill.enabled ? '启用' : '停用') + '</span></div>' +
      '<p class="dim-note">' + esc(skill.key || skill.id) + ' · 第 ' + esc(skill.version) + ' 版 · ' + esc(skill.source === 'builtin' ? '系统内置' : '作者添加') + '</p>' +
      '<p>' + esc(skill.description || '无描述') + '</p><details><summary>查看创作方法</summary><p class="readable-skill-instructions" style="white-space:pre-wrap;line-height:1.7;margin-top:8px">' + esc(skill.instructions || '暂无补充说明。') + '</p></details>' +
      '<div class="row row-wrap mt8">' + (skill.source === 'builtin' ? '<span class="badge badge-muted">系统内置，只读</span>' : '<button class="btn btn-sm btn-secondary" onclick="editSkill(\'' + escAttr(skill.id) + '\')">编辑</button>') +
      '<button class="btn btn-sm btn-secondary" onclick="toggleSkill(\'' + escAttr(skill.id) + '\',' + Boolean(skill.enabled) + ')">' + (skill.enabled ? '停用' : '启用') + '</button>' +
      (skill.source === 'builtin' ? '' : '<button class="btn btn-sm btn-danger" onclick="deleteSkill(\'' + escAttr(skill.id) + '\')">删除</button>') + '</div></div>'
    ).join('') || '<div class="card"><p class="dim-note">暂无 Skill。可在下方添加自己的指令模块。</p></div>';
    const mcpRows = state.mcpServers.map((server) =>
      '<div class="card"><div class="card-title-row"><h3>' + esc(server.name) + '</h3><span class="badge ' + (server.enabled ? 'badge-success' : 'badge-muted') + '">' + (server.enabled ? '启用' : '停用') + '</span></div>' +
      '<p class="dim-note">' + esc(server.transport) + (server.command ? ' · ' + esc(server.command) : '') + (server.url ? ' · ' + esc(server.url) : '') + '</p>' +
      '<p class="dim-note">凭据只接受 env:NAME 引用，不会把密钥写入数据库。</p><div id="mcp-validation-' + escAttr(server.id) + '" class="dim-note"></div>' +
      '<div class="row row-wrap mt8"><button class="btn btn-sm btn-secondary" onclick="editMcpServer(\'' + escAttr(server.id) + '\')">编辑</button>' +
      '<button class="btn btn-sm btn-secondary" onclick="validateMcpServer(\'' + escAttr(server.id) + '\')">检查配置</button>' +
      '<button class="btn btn-sm btn-secondary" onclick="toggleMcpServer(\'' + escAttr(server.id) + '\',' + Boolean(server.enabled) + ')">' + (server.enabled ? '停用' : '启用') + '</button>' +
      '<button class="btn btn-sm btn-danger" onclick="deleteMcpServer(\'' + escAttr(server.id) + '\')">删除</button></div></div>'
    ).join('') || '<div class="card"><p class="dim-note">暂无 MCP 配置。</p></div>';
    p.innerHTML = header('AI 配置', '统一管理 Provider、模型路由、结构化系统提示词、Skill 与 MCP；Provider 详情仍在本页入口维护，导航不再重复展示。', '<button class="btn btn-secondary" onclick="go(\'services\')">管理 Provider / Model</button><button class="btn btn-primary" onclick="saveAgentRoutes()">保存 Agent 路由</button>') +
      '<div class="content"><div class="card"><div class="card-title-row"><div><h3>全局 Provider</h3><p class="dim-note">同一供应商可添加多个账户或 Base URL；请给每个 Provider 使用不同名称。</p></div><span>' + providers + '</span></div><div class="grid grid-2"><div><p class="dim-note">模型、Skill 和 MCP 定义都存储在全局注册表，不绑定某一部作品。</p></div><div>' + modelSummary + '</div></div></div>' +
      '<h2 class="section-title">Agent 模型路由与系统提示词</h2><div id="agent-route-list" class="grid grid-2">' + routeCards + '</div>' +
      '<h2 class="section-title">全局 Skill</h2><div class="card"><div class="card-title-row"><div><h3>导入 Skill 包</h3><p class="dim-note">支持直接粘贴 GitHub 仓库、blob、tree 或 release 链接，也支持已下载的 SKILL.md 文件夹、ZIP / TAR 包。只解析 Markdown/YAML，不执行脚本。</p></div><span class="badge badge-info">标准 SKILL.md</span></div><div class="grid grid-2"><label class="fld">GitHub 链接<input class="input" id="skill-github-url" placeholder="https://github.com/owner/repo/tree/main/skill"><button class="btn btn-primary mt8" onclick="importSkillFromGithub()">从 GitHub 导入</button></label><label class="fld">已下载的包<input class="input" id="skill-package-file" type="file" accept=".md,.zip,.tar,.gz,.tgz"><button class="btn btn-secondary mt8" onclick="importSkillPackage()">导入文件包</button></label></div><label class="fld">已下载的文件夹<input class="input" id="skill-folder-files" type="file" multiple webkitdirectory directory><button class="btn btn-secondary mt8" onclick="importSkillFolder()">导入文件夹</button></label></div><div class="card"><div class="grid grid-2"><input type="hidden" id="skill-edit-id"><label class="fld">名称<input class="input" id="skill-name" placeholder="例如：悬疑节奏检查"></label><label class="fld">Key<input class="input" id="skill-key" placeholder="例如：mystery-pacing"></label><label class="fld">描述<input class="input" id="skill-description"></label><label class="fld checkline"><input type="checkbox" id="skill-enabled" checked> 全局默认启用</label></div><label class="fld">Instructions<textarea class="input textarea" id="skill-instructions" rows="8" placeholder="写给 Agent 的可复用指令"></textarea></label><label class="fld">配置 JSON<textarea class="input textarea" id="skill-config" rows="4" placeholder="{}"></textarea></label><div class="row row-wrap"><button class="btn btn-primary" onclick="saveSkill()">保存 Skill</button><button class="btn btn-secondary" onclick="resetSkillForm()">清空表单</button></div></div><div class="grid grid-2">' + skillRows + '</div>' +
      '<h2 class="section-title">全局 MCP</h2><div class="card"><input type="hidden" id="mcp-edit-id"><div class="grid grid-2"><label class="fld">名称<input class="input" id="mcp-name" placeholder="例如：本地文件工具"></label><label class="fld">传输<select class="input" id="mcp-transport"><option value="stdio">stdio（本地命令）</option><option value="sse">SSE</option><option value="streamable_http">Streamable HTTP</option></select></label><label class="fld">Command<input class="input" id="mcp-command" placeholder="例如：npx"></label><label class="fld">URL<input class="input" id="mcp-url" placeholder="远程 MCP 的 http(s) URL"></label></div><label class="fld">Args（每行一个参数）<textarea class="input textarea" id="mcp-args" rows="4"></textarea></label><div class="grid grid-2"><label class="fld">Environment JSON<textarea class="input textarea" id="mcp-environment" rows="5" placeholder="请使用 JSON；敏感值写成 env:NAME"></textarea></label><label class="fld">Headers JSON<textarea class="input textarea" id="mcp-headers" rows="5" placeholder="例如：{&quot;Authorization&quot;:&quot;env:MCP_AUTHORIZATION&quot;}"></textarea></label></div><label class="fld checkline"><input type="checkbox" id="mcp-enabled" checked> 全局默认启用</label><p class="dim-note">环境变量可填写普通非敏感值；名称含 token / key / secret 等敏感字段时必须使用 env:NAME。请求头始终必须使用 env:NAME。</p><div class="row row-wrap"><button class="btn btn-primary" onclick="saveMcpServer()">保存 MCP</button><button class="btn btn-secondary" onclick="resetMcpForm()">清空表单</button></div></div><div class="grid grid-2">' + mcpRows + '</div><div class="warn-banner" style="margin-top:16px">MCP 当前完成了定义保存、启停和本地配置校验；真实服务器握手与工具调用仍未接入，页面会明确标注这一点。</div></div>';
  };

  const legacyCreatePage = PAGES.create;
  window.selectCreateGenre = function (value) {
    const custom = document.getElementById('c-custom-genre');
    const target = document.getElementById('c-genre');
    if (custom) custom.style.display = value === '__custom__' ? '' : 'none';
    if (target && value !== '__custom__') target.value = value;
    if (target && value === '__custom__' && custom) target.value = custom.value;
  };
  PAGES.create = async (p) => {
    await legacyCreatePage(p);
    try {
      const preflight = await api('GET', '/creation/preflight?mode=planned');
      if (!preflight.ready) {
        const content = p.querySelector('.content');
        const gate = document.createElement('div');
        gate.className = 'warn-banner planning-gate mb16';
        gate.innerHTML = '<div class="planning-gate-content"><b>开始前请先配置 LLM 供应商</b><span>' + esc(preflight.modelReadiness?.message || '三种创作入口都必须先接入可用的模型。') + '</span><span class="dim-note">缺少路由：' + esc((preflight.modelReadiness?.missingRoles || []).join('、') || '供应商 / 模型') + '</span><div class="planning-gate-actions"><button class="btn btn-sm btn-primary" onclick="go(\'agent-config\')">打开 AI 配置</button></div></div>';
        content?.prepend(gate);
      }
    } catch (error) {
      const content = p.querySelector('.content');
      const gate = document.createElement('div');
      gate.className = 'warn-banner planning-gate mb16';
      gate.innerHTML = '<div class="planning-gate-content"><b>无法确认 AI Runtime 状态</b><span>' + esc(error?.message || '创作前置检查失败，请先打开 AI 配置确认 Runtime。') + '</span><div class="planning-gate-actions"><button class="btn btn-sm btn-primary" onclick="go(\'agent-config\')">打开 AI 配置</button></div></div>';
      content?.prepend(gate);
    }
    const field = p.querySelector('#c-genre')?.closest('.field');
    if (!field) return;
    let genres = [];
    try {
      const result = await api('GET', '/genres');
      genres = result.genres || [];
    } catch (error) {
      toast('题材列表读取失败：' + (error?.message || '未知错误'), 'error');
    }
    const current = p.querySelector('#c-genre')?.value || '';
    field.innerHTML = '<label>题材</label><select class="input" id="c-genre-select" onchange="selectCreateGenre(this.value)"><option value="">请选择内置题材</option>' +
      genres.map((genre) => '<option value="' + escAttr(genre.id || genre.key) + '">' + esc(genre.name) + ' · ' + esc((genre.tags || []).slice(0, 3).join(' / ')) + '</option>').join('') +
      '<option value="__custom__">自定义题材</option></select><input class="input mt8" id="c-custom-genre" placeholder="填写自定义题材" style="display:none"><input type="hidden" id="c-genre" value="' + escAttr(current) + '">';
    const select = field.querySelector('#c-genre-select');
    const option = [...(select?.options || [])].find((item) => item.value === current);
    if (option) select.value = current;
    else if (current) { select.value = '__custom__'; field.querySelector('#c-custom-genre').value = current; window.selectCreateGenre('__custom__'); }
    field.querySelector('#c-custom-genre')?.addEventListener('input', (event) => {
      const target = field.querySelector('#c-genre');
      if (target && select?.value === '__custom__') target.value = event.target.value;
    });
  };

  const legacyAgentConfigPage = PAGES['agent-config'];
  PAGES['agent-config'] = async (p) => {
    await legacyAgentConfigPage(p);
    const config = extensionState.config || {};
    const content = p.querySelector('.content');
    if (!content) return;
    p.querySelectorAll('[onclick*="services"]').forEach((element) => element.remove());
    const pairs = [];
    (config.providers || []).forEach((provider) => {
      const providerModels = (config.models || []).filter((model) => model.providerId === provider.id);
      if (providerModels.length) providerModels.forEach((model) => pairs.push([provider, model]));
      else pairs.push([provider, {}]);
    });
    (config.models || []).filter((model) => !(config.providers || []).some((provider) => provider.id === model.providerId))
      .forEach((model) => pairs.push([{}, model]));
    if (!pairs.length) pairs.push([{ id: newModelId('provider'), providerType: 'custom' }, {}]);
    window.__savedProviderIds = new Set((config.providers || []).map((item) => item.id));
    window.__savedModelIds = new Set((config.models || []).map((item) => item.id));
    window.__providerPresets = config.presets || [];
    const providerSection = document.createElement('div');
    providerSection.className = 'card';
    providerSection.id = 'provider-editor';
    providerSection.innerHTML =
      '<div class="card-title-row"><div><h2>供应商与模型管理</h2><p class="dim-note">先保存供应商，再手动添加或获取模型；可以随时删除供应商、移除模型，或填写新密钥替换旧密钥。</p></div>' +
      '<div class="row row-wrap"><select class="input" id="provider-preset" style="width:180px"><option value="">添加 Provider 模板</option>' +
      (window.__providerPresets || []).map((item) => '<option value="' + escAttr(item.id) + '">' + esc(item.name) + '</option>').join('') +
      '</select><button class="btn btn-secondary" onclick="addProviderPreset()">使用模板</button><button class="btn btn-secondary" onclick="addProviderModel()">添加自定义 Provider</button><button class="btn btn-primary" onclick="saveServices()">保存全部配置</button></div></div>' +
      '<div class="warn-banner">访问密钥不会回显；首个供应商可以先保存，模型可以随后手动添加或从服务端获取。</div>' +
      '<div class="provider-editor-body"><div id="provider-model-list" class="grid grid-2">' + pairs.map((pair) => providerModelCard(pair[0], pair[1])).join('') + '</div>' +
      '<aside class="provider-guide" aria-label="Provider 配置流程"><div class="provider-guide-kicker">配置流程</div><h3>把模型接入写作链路</h3><p>先完成一个可用 Provider，再为规划、写作和审查分配模型。</p><ol class="provider-guide-steps"><li>添加 Provider 和 Model，填写服务地址。</li><li>获取模型列表，测试连接是否正常。</li><li>保存 Agent 路由，开始使用创作管线。</li></ol><div class="provider-guide-note"><b>安全边界</b><span>API Key 只保存到受保护存储，页面不会回显。</span></div></aside></div>';
    content.prepend(providerSection);
    const runtimeSection = document.createElement('section');
    runtimeSection.className = 'card runtime-center-card';
    runtimeSection.id = 'runtime-center';
    runtimeSection.innerHTML = '<div class="loading"><div class="spinner"></div>正在读取 Runtime Plane 状态…</div>';
    content.prepend(runtimeSection);
    try {
      const [registryState, capabilityState, policyState, toolState, telemetryState] = await Promise.all([
        api('GET', '/runtime/registry'),
        api('GET', '/runtime/capabilities'),
        api('GET', '/compute/policy'),
        api('GET', '/runtime/tools'),
        api('GET', '/compute/telemetry'),
      ]);
      const capabilities = Object.fromEntries((capabilityState.runtimes || []).map((item) => [item.runtimeType, item]));
      const runtimeRows = (registryState.runtimes || []).map((item) => {
        const manifest = item.manifest || {};
        const installation = item.installation || {};
        const capability = capabilities[manifest.runtimeType] || {};
        const runtimeType = escAttr(manifest.runtimeType);
        const controls = [];
        if (installation.state === 'not_installed') {
          controls.push('<button class="btn btn-sm btn-secondary" onclick="runtimePlaneAction(\'' + runtimeType + '\',\'install\')">安装 / 发现</button>');
        } else {
          controls.push('<button class="btn btn-sm btn-ghost" onclick="runtimePlaneAction(\'' + runtimeType + '\',\'discover\')">重新发现</button>');
          controls.push('<button class="btn btn-sm btn-ghost" onclick="runtimePlaneAction(\'' + runtimeType + '\',\'reconnect\')">重新连接</button>');
          if (manifest.authentication?.type !== 'local-no-auth') {
            controls.push('<button class="btn btn-sm btn-ghost" onclick="runtimePlaneAction(\'' + runtimeType + '\',\'reauthenticate\')">重新认证</button>');
          }
          if (installation.state === 'broken' || installation.state === 'needs_update') {
            controls.push('<button class="btn btn-sm btn-ghost" onclick="runtimePlaneAction(\'' + runtimeType + '\',\'repair\')">修复 / 更新</button>');
          }
          if (['installed', 'authenticated', 'capability_verified', 'ready', 'needs_update'].includes(installation.state) && manifest.acquisition !== 'builtin' && manifest.acquisition !== 'bundled') {
            controls.push('<button class="btn btn-sm btn-ghost" onclick="runtimePlaneAction(\'' + runtimeType + '\',\'update\')">更新</button>');
          }
          const hasSupervisedUninstall = manifest.acquisition === 'download_binary' || Array.isArray(manifest.installer?.uninstallCommand);
          if (hasSupervisedUninstall) {
            controls.push('<button class="btn btn-sm btn-ghost" onclick="runtimePlaneAction(\'' + runtimeType + '\',\'uninstall\')">卸载</button>');
          }
        }
        controls.unshift('<button class="btn btn-sm btn-ghost" onclick="runtimePlaneDiagnostics(\'' + runtimeType + '\')">详情</button>');
        const action = '<div class="row row-wrap mt8" style="justify-content:flex-end">' + controls.join('') + '</div>';
        const verification = installation.verified ? ' · 已校验' : '';
        return '<div class="list-row runtime-row" style="align-items:flex-start"><div><b>' + esc(manifest.displayName || manifest.runtimeType) + '</b><div class="dim-note mt8">' + esc(manifest.protocol || 'unknown') + ' · ' + esc(manifest.acquisition || 'unknown') + ' · source:' + esc(manifest.sourceKind || installation.sourceKind || 'unknown') + verification + (installation.path ? ' · ' + esc(installation.path) : '') + '</div></div><div class="spacer"></div><div style="text-align:right">' + statusBadge(installation.state || 'unknown') + '<div class="dim-note mt8">' + esc((capability.integrationGrade || manifest.integrationGrade || '—') + ' · ' + (capability.models || []).length + ' models') + '</div>' + action + '</div></div>';
      }).join('') || '<p class="dim-note">暂无 Runtime manifest。</p>';
      const toolRows = (toolState.tools || []).map((tool) => '<span class="badge ' + (tool.authority === 'authority' ? 'badge-warning' : tool.authority === 'proposal' ? 'badge-info' : 'badge-muted') + '">' + esc(tool.name) + ' · ' + esc(tool.authority) + '</span>').join('');
      const telemetryRows = (telemetryState.summary || []).slice(0, 12).map((item) =>
        '<div class="list-row"><span>' + esc(item.taskType || 'unknown') + '</span><span>' + esc(item.runtimeType || 'runtime') + ' / ' + esc(item.modelId || 'unknown') + '</span><span class="text-sm text-muted">' + esc(item.reasoning || 'unknown') + ' · runs ' + esc(String(item.runs ?? 0)) + ' · success ' + esc(String(item.successRate ?? 0)) + '</span></div>'
      ).join('') || '<span class="dim-note">暂无 AgentRun telemetry；完成任务后这里会显示成功率、成本和延迟摘要。</span>';
      const strategyButtons = (policyState.strategies || []).map((strategy) => '<button class="btn btn-sm ' + (strategy.id === policyState.strategy ? 'btn-primary' : 'btn-ghost') + '" onclick="computePolicySelect(\'' + escAttr(strategy.id) + '\')" title="' + escAttr(strategy.description || '') + '">' + esc(strategy.name || strategy.id) + '</button>').join('');
       runtimeSection.innerHTML = runtimeOnboardingCard(registryState, capabilityState) +
         '<div class="card-title-row"><div><h2>Runtime Center / Marketplace</h2><p class="dim-note">Runtime、模型、Reasoning 与 NovelForge 领域权限分层；运行时状态来自持久化 Registry，不把 manifest 当作已就绪。</p></div><div class="row row-wrap"><input class="input" id="runtime-catalog-url" placeholder="https://trusted.example/runtime-catalog.json" style="width:280px"><button class="btn btn-sm btn-secondary" onclick="runtimeCatalogFetch()">导入签名 Catalog</button><span class="badge badge-info">Control / Compute / Runtime</span></div></div>' +
        '<div class="grid grid-2"><div><h3>Runtime Registry</h3>' + runtimeRows + '</div><div><h3>Compute Strategy · ' + esc(policyState.strategyName || policyState.strategy || '—') + '</h3><div class="row row-wrap mt8">' + strategyButtons + '</div><p class="dim-note mt8">' + esc((policyState.strategies || []).find((item) => item.id === policyState.strategy)?.description || '策略由 Compute Scheduler 执行。') + '</p><div class="kv"><span>Capability</span><b>' + esc(policyState.floor) + ' → ' + esc(policyState.preferred) + ' → ' + esc(policyState.ceiling) + '</b></div><div class="kv"><span>Critical floor</span><b>' + esc(policyState.criticalFloor) + '（所有策略均不可绕过）</b></div><div class="kv"><span>Agent escalation requests</span><b>' + (policyState.allowAgentEscalation ? '允许（需 Host 审批）' : '禁止') + '</b></div><div class="kv"><span>Budget</span><b>' + esc(String(policyState.budget?.available ?? '—')) + ' NF_CU available · ' + esc(policyState.budgetMode || 'hard') + '</b></div><p class="dim-note mt8">角色默认交给 Scheduler：Planner · Writer · Reviewer · Fact Extractor · Image · Embedding · Reranker。</p></div></div>' +
        '<div class="workspace-section"><b>Tool Gateway catalog</b><div class="row row-wrap mt8">' + (toolRows || '<span class="dim-note">暂无工具</span>') + '</div><p class="dim-note mt8">Authority 工具必须同时满足任务 allowlist、Canon-write 约束、运行时批准和作者确认；Agent 不能直接写 SQLite。</p></div>' +
        '<div class="workspace-section"><b>Compute telemetry（只读证据）</b><div class="mt8">' + telemetryRows + '</div><p class="dim-note mt8">用于未来自适应调度的历史观察；当前 Scheduler 仍以显式 Policy、Capability 与 Budget 为准。</p></div>';
    } catch (error) {
      runtimeSection.innerHTML = '<div class="warn-banner" style="border-color:var(--error);color:var(--error)"><b>Runtime Plane 状态读取失败。</b><span>' + esc(error.message || 'unknown error') + '</span></div>';
    }
    const routeList = p.querySelector('#agent-route-list');
    if (routeList) {
      const roles = config.roles || Object.keys(MODEL_ROLES);
      const overrides = config.routePromptOverrides || {};
      const defaults = config.defaultRoutePrompts || {};
      const effective = config.effectiveRoutePrompts || {};
      routeList.className = 'grid grid-2';
      routeList.innerHTML = roles.map((role) => {
        const override = overrides[role] || '';
        return '<div class="card"><div class="card-title-row"><h3>' + esc(MODEL_ROLES[role] || role) + '</h3><span class="badge badge-muted">' + esc(role) + ' · v' + esc((config.routePromptVersions || {})[role] || 'builtin-1') + '</span></div>' +
          '<label class="fld">模型<select class="input route-model" data-role="' + escAttr(role) + '">' + extensionModelOptions(config.models, (config.routes || {})[role]) + '</select></label>' +
          '<label class="fld">用户补充规则（Route Override）<textarea class="input textarea" data-role="' + escAttr(role) + '" rows="8" placeholder="可留空；系统模板始终保留">' + esc(override) + '</textarea></label>' +
          '<p class="dim-note mt8">平台会自动保留这个角色的系统职责；你只需填写上面的补充规则。</p>' +
          '<div class="row row-wrap mt8"><button class="btn btn-sm btn-secondary" onclick="resetAgentPrompt(\'' + escAttr(role) + '\')">清空补充规则</button><span class="dim-note">短旧配置会自动作为补充规则迁移。</span></div></div>';
      }).join('');
    }
    content.innerHTML = content.innerHTML
      .replaceAll('Markdown Agent Contract', '系统职责规则')
      .replaceAll('SKILL.md', 'Skill 文件')
      .replaceAll('Markdown/YAML', '文本规则')
      .replaceAll('配置 JSON', '补充配置')
      .replaceAll('Environment JSON', '环境变量')
      .replaceAll('Headers JSON', '请求头')
      .replaceAll('Route Override', '补充规则')
      .replaceAll('API Key', '访问密钥');
    const skillConfig = content.querySelector('#skill-config');
    if (skillConfig) skillConfig.placeholder = '可选，留空使用默认设置';
    const mcpEnvironment = content.querySelector('#mcp-environment');
    if (mcpEnvironment) mcpEnvironment.placeholder = '每行填写一项，敏感值使用环境变量名';
    const mcpHeaders = content.querySelector('#mcp-headers');
    if (mcpHeaders) mcpHeaders.placeholder = '每行填写一项请求头，敏感值使用环境变量名';
  };

  window.runtimePlaneDiagnostics = async function (runtimeType) {
    try {
      const data = await api('GET', '/runtime/' + encodeURIComponent(runtimeType) + '/diagnostics');
      const manifest = data.manifest || {};
      const installation = data.installation || {};
      const auth = installation.auth || {};
      const checks = (data.prerequisites?.checks || []).map((item) => '<div class="list-row"><span>' + esc(item.name || 'prerequisite') + '</span><span class="badge ' + (item.available ? 'badge-success' : (item.required ? 'badge-error' : 'badge-warning')) + '">' + (item.available ? '可用' : (item.required ? '缺失' : '可选')) + '</span><span class="dim-note">' + esc(item.detail || '') + '</span></div>').join('') || '<p class="dim-note">没有声明前置依赖。</p>';
      const plans = Object.entries(data.plans || {}).map(([action, plan]) => {
        const command = Array.isArray(plan.command) && plan.command.length ? '<div class="mono mt8">命令：' + esc(plan.command.join(' ')) + '</div>' : '';
        const artifact = plan.artifactUrl ? '<div class="mono mt8">Artifact：' + esc(plan.artifactUrl) + ' → ' + esc(plan.artifactPath || '未声明目标') + '<br>SHA-256：' + esc(plan.artifactSha256 || '未声明') + '</div>' : '';
        return '<div class="list-row" style="display:block"><div class="row"><b>' + esc(action) + '</b><span class="spacer"></span><span class="badge ' + (plan.allowed ? 'badge-success' : 'badge-error') + '">' + (plan.allowed ? '允许' : '拒绝') + '</span><span class="badge badge-muted">' + esc(plan.risk || '—') + '</span></div><div class="dim-note mt8">' + esc(plan.explanation || '') + '</div>' + command + artifact + '</div>';
      }).join('') || '<p class="dim-note">没有可用操作计划。</p>';
      const events = [...(data.events || [])].reverse().slice(0, 30).map((event) => '<details class="mt8"><summary>' + esc(event.createdAt || '') + ' · ' + esc(event.action || '') + ' · ' + esc(event.phase || '') + ' · ' + esc(event.status || '') + '</summary><p class="text-sm mt8">' + esc(event.message || '') + '</p>' + (event.detail && Object.keys(event.detail).length ? '<pre class="mono" style="white-space:pre-wrap;max-height:240px;overflow:auto">' + pretty(event.detail) + '</pre>' : '') + '</details>').join('') || '<p class="dim-note">暂无安装事件。</p>';
      modal('<div class="modal-header"><div><h3>' + esc(manifest.displayName || runtimeType) + ' · Runtime 详情</h3><p class="dim-note">Manifest、安装状态、认证、前置检查和可审阅操作计划</p></div><button class="close-x" onclick="closeModal()">×</button></div>' +
        '<div class="grid grid-2"><div class="card"><h4>Observed installation</h4><div class="kv"><span>状态</span><b>' + statusBadge(installation.state || 'unknown') + '</b></div><div class="kv"><span>路径</span><b class="mono">' + esc(installation.path || '—') + '</b></div><div class="kv"><span>版本</span><b>' + esc(installation.version || manifest.version || '—') + '</b></div><div class="kv"><span>健康</span><b>' + esc(installation.health || '—') + '</b></div><div class="kv"><span>Artifact verified</span><b>' + (installation.verified ? '是' : '否') + '</b></div><div class="kv"><span>Auth</span><b>' + esc(auth.status || 'unknown') + '</b></div><p class="dim-note mt8">' + esc(auth.detail || installation.lastError || '') + '</p></div>' +
        '<div class="card"><h4>Manifest</h4><div class="kv"><span>Protocol</span><b>' + esc(manifest.protocol || '—') + '</b></div><div class="kv"><span>Acquisition</span><b>' + esc(manifest.acquisition || '—') + '</b></div><div class="kv"><span>Source</span><b>' + esc(manifest.sourceKind || '—') + '</b></div><div class="kv"><span>Integration grade</span><b>' + esc(manifest.integrationGrade || '—') + '</b></div><div class="kv"><span>Compatibility</span><b>' + esc(data.compatibility?.compatible ? 'compatible' : (data.compatibility?.reason || 'unknown')) + '</b></div></div></div>' +
        '<div class="divider"></div><h4>Prerequisites</h4><div class="mt8">' + checks + '</div><div class="divider"></div><h4>审阅操作计划</h4><div class="mt8">' + plans + '</div><div class="divider"></div><h4>Embedded Installation Console</h4><div class="card mt8">' + events + '</div>', true);
    } catch (error) { toast(error.message || 'Runtime diagnostics failed', 'error'); }
  };

  window.runtimeOpenApiSetup = function () {
    const target = document.getElementById('provider-editor');
    if (!target) return;
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    target.querySelector('#provider-preset, .pm-name')?.focus();
  };
  window.runtimeOnboardingAction = async function (runtimeType, action) {
    await window.runtimePlaneAction(runtimeType, action);
  };
  window.runtimePlaneAction = async function (runtimeType, action) {
    try {
      const requiresApproval = ['install', 'repair', 'update', 'uninstall'].includes(action);
      if (requiresApproval) {
        const diagnostics = await api('GET', '/runtime/' + encodeURIComponent(runtimeType) + '/diagnostics');
        const plan = diagnostics.plans?.[action] || {};
        const command = Array.isArray(plan.command) && plan.command.length ? '\n命令：' + plan.command.join(' ') : (plan.artifactUrl ? '\n下载：' + plan.artifactUrl + '\n目标：' + (plan.artifactPath || '未声明') + '\nSHA-256：' + (plan.artifactSha256 || '未声明') : '\n本次操作不声明外部命令，将由 Runtime Registry 进行连接/发现。');
        const trust = plan.allowed === false ? '\n该计划被主机安全策略拒绝。' : (plan.trusted ? '\n来源：已信任。' : '\n来源：未信任，必须由作者明确批准。');
        if (!window.confirm('确认执行 Runtime ' + action + '：' + runtimeType + '？' + command + trust)) return;
      }
      const result = await api('POST', '/runtime/' + encodeURIComponent(runtimeType) + '/' + action, requiresApproval ? { approved: true } : undefined);
      const state = result.installation?.state || (result.ready ? 'ready' : 'runtime state updated');
      const detail = result.auth?.detail ? ' · ' + result.auth.detail : '';
      toast(state + ' · ' + runtimeType + detail, result.ready === false && result.auth?.status === 'not_authenticated' ? 'error' : 'success');
      const page = document.getElementById('page');
      if (page && typeof PAGES['agent-config'] === 'function') await PAGES['agent-config'](page);
    } catch (error) { toast(error.message || 'Runtime operation failed', 'error'); }
  };
  window.computePolicySelect = async function (strategy) {
    try {
      const result = await api('POST', '/compute/policy', { strategy });
      toast('Compute Strategy 已切换为 ' + (result.strategyName || strategy), 'success');
      const page = document.getElementById('page');
      if (page && typeof PAGES['agent-config'] === 'function') await PAGES['agent-config'](page);
    } catch (error) { toast(error.message || 'Compute Strategy 更新失败', 'error'); }
  };
  window.runtimeCatalogFetch = async function () {
    const input = document.getElementById('runtime-catalog-url');
    const url = String(input?.value || '').trim();
    if (!url) { toast('请输入签名 Runtime Catalog URL', 'error'); return; }
    if (!window.confirm('从该 HTTPS 地址获取并验证签名 Catalog？\n' + url)) return;
    try {
      const result = await api('POST', '/runtime/catalog/fetch', { url });
      toast('已导入 ' + String(result.count || 0) + ' 个 Runtime manifest', 'success');
      const page = document.getElementById('page');
      if (page && typeof PAGES['agent-config'] === 'function') await PAGES['agent-config'](page);
    } catch (error) { toast(error.message || 'Runtime Catalog fetch failed', 'error'); }
  };
  // Deep links and old scripts may still call services; render the unified page.
  PAGES.services = PAGES['agent-config'];

  window.requestDraftAdjustmentPlan = async function (importId) {
    try {
      const queued = await api('POST', '/books/' + encodeURIComponent(S.book) + '/draft-imports/' + encodeURIComponent(importId) + '/adjustment-plan');
      const task = await waitForTask(queued.taskId, () => {});
      toast(task.status === 'completed' ? '后续调整方案已生成' : '调整方案任务未完成', task.status === 'completed' ? 'success' : 'error');
      await window.showDraftImportReport(importId);
    } catch (error) { toast(error.message, 'error'); }
  };
  window.showDraftImportReport = async function (importId) {
    try {
      const data = await api('GET', '/books/' + encodeURIComponent(S.book) + '/draft-imports/' + encodeURIComponent(importId));
      const item = data.draftImport || {};
      const report = item.report || {};
      const coverage = report.coverage || {};
      const dimensions = report.dimensions || report.drift_dimensions || [];
      const findings = report.chapter_findings || [];
      const manifest = report.chapter_manifest || report.chapterManifest || [];
      const evidence = report.evidence || [];
      const plan = report.continuation_plan || {};
      const adjustment = report.adjustment_plan;
      const dimensionHtml = dimensions.map((entry) => '<div class="card"><b>' + esc(entry.dimension || entry.name || '未命名') + ' · ' + esc(entry.severity || '') + '</b><p class="dim-note mt8">' + esc(entry.impact || '') + '</p><p>' + esc(entry.recommendation || '') + '</p>' + (entry.evidence || []).map((source) => '<blockquote>' + esc(source.source || '') + '：' + esc(source.quote || '') + '</blockquote>').join('') + '</div>').join('') || '<p class="dim-note">没有返回具体维度问题。</p>';
      const findingHtml = findings.map((entry) => '<div class="list-row"><span class="badge ' + (entry.status === 'drift' ? 'badge-error' : 'badge-info') + '">' + esc(entry.status || 'needs_review') + '</span><div><b>' + esc(entry.chapter_label || entry.chapterLabel || entry.source || '') + '</b><div class="dim-note">' + esc((entry.issues || []).join('；')) + '</div></div></div>').join('') || '<p class="dim-note">没有章节级发现。</p>';
      const manifestHtml = manifest.map((entry) => '<div class="list-row"><span class="badge badge-muted">' + esc(entry.chapter_label || entry.chapterLabel || ('#' + (entry.sequence || ''))) + '</span><span>' + esc(entry.relative_path || entry.relativePath || '') + '</span><span class="spacer"></span><span class="dim-note">' + esc(entry.character_count || entry.characterCount || 0) + ' 字符 · ' + esc(entry.recognition || 'unrecognized') + ((entry.warnings || []).length ? ' · ' + esc(entry.warnings.join('；')) : '') + '</span></div>').join('') || '<p class="dim-note">暂无可识别章节清单。</p>';
      const evidenceHtml = evidence.map((entry) => '<details class="mt8"><summary>' + esc(entry.window_id || entry.source || '证据') + '</summary><div class="mt8">' + readableProjection(entry) + '</div></details>').join('') || '<p class="dim-note">没有可展示的证据对象。</p>';
      const adjustmentHtml = adjustment ? '<div class="card"><h4>待审阅后续调整方案</h4><div class="mt8">' + readableProjection(adjustment) + '</div><p class="dim-note">该方案只用于规划，不会自动修改 Story Bible 或正式章节。</p></div>' : (item.status === 'completed' ? '<button class="btn btn-primary" onclick="requestDraftAdjustmentPlan(\'' + escAttr(item.id) + '\')">生成待审阅后续调整方案</button>' : '');
      modal('<div class="modal-header"><div><h3>初稿偏移分析</h3><p class="dim-note">' + esc(item.status || '') + ' · ' + esc(report.verdict || 'insufficient_evidence') + ' · 偏移分 ' + esc(report.drift_score == null ? '—' : report.drift_score) + ' · 置信度 ' + esc(report.confidence == null ? '—' : report.confidence) + '</p></div><button class="close-x" onclick="closeModal()">×</button></div>' +
        '<p>' + esc(report.summary || '暂无摘要') + '</p><div class="card mt16"><h4>覆盖率</h4><p class="dim-note">文件：' + esc(coverage.analyzed_files || 0) + ' / ' + esc(coverage.total_files || 0) + '；章节：' + esc(coverage.analyzed_chapters || 0) + ' / ' + esc(coverage.total_chapters || 0) + '；窗口：' + esc((coverage.completed_windows || []).length) + ' / ' + esc((coverage.windows || []).length) + '；截断：' + esc(coverage.truncated_items || 0) + '</p><p class="dim-note">来源优先级：Story Bible 100 · 语言总览 90 · 初稿正文 50</p></div>' +
        '<div class="divider"></div><h4>章节清单</h4><div class="mt16">' + manifestHtml + '</div><div class="divider"></div><h4>章节级发现</h4><div class="mt16">' + findingHtml + '</div><div class="divider"></div><h4>维度问题</h4><div class="mt16">' + dimensionHtml + '</div><div class="divider"></div><h4>证据来源</h4>' + evidenceHtml + '<div class="divider"></div><h4>后续计划</h4><ul style="margin-left:18px;line-height:1.9">' + (plan.repair_first || []).map((entry) => '<li>先修：' + esc(entry) + '</li>').join('') + (plan.next_chapters || []).map((entry) => '<li>续写：' + esc(entry) + '</li>').join('') + (plan.do_not_change || []).map((entry) => '<li>保留：' + esc(entry) + '</li>').join('') + '</ul><p class="dim-note mt16">局限：' + (report.limitations || []).map((entry) => esc(entry)).join('；') + '</p><div class="row row-wrap mt16">' + adjustmentHtml + '</div>', true);
    } catch (error) { toast(error.message, 'error'); }
  };

  renderNav();
  // When the Workbench loader is present it intentionally initializes the
  // shell only after every page adapter has registered.  Rendering here as
  // well races that first shell render (a slow dashboard request can
  // overwrite a deep-linked AI Runtime page).  Standalone legacy loading
  // still owns its initial render when no shell has been initialized.
  if (!window.__novelforgeStudioShell || window.StudioShell) render();
})();
