/**
 * 主应用模块 - 路由和状态管理
 */

const App = (() => {
  let _appInitialized = false;

  async function init() {
    // 初始化 Auth
    Auth.init(onLoginSuccess);

    // 检查登录状态
    const user = await Auth.checkSession();
    if (user) {
      showApp(user);
    } else {
      showAuth();
    }
  }

  function onLoginSuccess(result) {
    showApp({ user_id: result.user_id, username: result.username });
  }

  function showAuth() {
    document.getElementById('auth-view').style.display = 'flex';
    document.getElementById('app-view').style.display = 'none';
  }

  function showApp(user) {
    document.getElementById('auth-view').style.display = 'none';
    document.getElementById('app-view').style.display = 'flex';

    // 设置用户信息
    document.getElementById('username-display').textContent = user.username;
    document.getElementById('user-avatar').textContent = user.username.charAt(0).toUpperCase();

    // 只初始化一次，避免事件监听器累积
    if (!_appInitialized) {
      _appInitialized = true;

      Chat.init();
      Files.init();
      VoiceAgent.init();
      HumanChat.init();

      // 退出按钮
      document.getElementById('btn-logout').addEventListener('click', Auth.logout);

      // Voice Agent Mode 按钮
      document.getElementById('btn-voice-agent').addEventListener('click', () => {
        const convId = Chat.getCurrentConvId();
        VoiceAgent.open(convId);
      });

      // HumanChat Mode 按钮
      document.getElementById('btn-humanchat').addEventListener('click', () => {
        const convId = Chat.getCurrentConvId();
        HumanChat.toggle(convId);
      });

      // System Prompt 设置按钮
      document.getElementById('btn-settings').addEventListener('click', openPromptEditor);

      // Batch Run 按钮
      document.getElementById('btn-batch-run').addEventListener('click', openBatchRunner);

      // User Profile 按钮
      document.getElementById('btn-user-profile').addEventListener('click', openProfileEditor);
      checkProfileStatus();

      // Subagent 管理按钮
      document.getElementById('btn-subagents').addEventListener('click', openSubagentManager);

      // 文件面板切换
      const filePanel = document.getElementById('file-panel');
      const toggleBtn = document.getElementById('btn-toggle-files');
      const resizeHandle = document.getElementById('fp-resize-handle');

      function _syncHandleVis() {
        if (filePanel.classList.contains('hidden')) {
          resizeHandle.classList.add('hidden');
        } else {
          resizeHandle.classList.remove('hidden');
        }
      }

      toggleBtn.addEventListener('click', () => {
        filePanel.classList.toggle('hidden');
        toggleBtn.classList.toggle('active');
        _syncHandleVis();
        filePanel.style.removeProperty('width');
      });

      // 默认隐藏
      filePanel.classList.add('hidden');
      resizeHandle.classList.add('hidden');

      // 拖拽调整宽度
      let _dragging = false;
      resizeHandle.addEventListener('mousedown', (e) => {
        e.preventDefault();
        _dragging = true;
        resizeHandle.classList.add('dragging');
        document.body.classList.add('fp-resizing');
        filePanel.style.transition = 'none';
      });
      document.addEventListener('mousemove', (e) => {
        if (!_dragging) return;
        const containerRight = document.getElementById('app-view').getBoundingClientRect().right;
        const newWidth = containerRight - e.clientX;
        const minW = 260;
        const maxW = window.innerWidth * 0.75;
        filePanel.style.width = Math.max(minW, Math.min(maxW, newWidth)) + 'px';
      });
      document.addEventListener('mouseup', () => {
        if (!_dragging) return;
        _dragging = false;
        resizeHandle.classList.remove('dragging');
        document.body.classList.remove('fp-resizing');
        filePanel.style.transition = '';
      });
    }

    // 加载数据（每次登录都刷新）
    Chat.loadConversations();
  }

  // ===== Toast 通知 =====
  function toast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    container.appendChild(el);

    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateX(30px)';
      el.style.transition = 'all 0.3s ease';
      setTimeout(() => el.remove(), 300);
    }, 3000);
  }

  // ===== Modal =====
  let _modalEscHandler = null;
  let _modalOverlayHandler = null;

  function modal(title, bodyHtml, buttons = []) {
    const overlay = document.getElementById('modal-overlay');
    const modalTitle = document.getElementById('modal-title');
    const modalBody = document.getElementById('modal-body');
    const modalFooter = document.getElementById('modal-footer');

    modalTitle.textContent = title;
    modalBody.innerHTML = bodyHtml;
    modalFooter.innerHTML = '';

    for (const btn of buttons) {
      const el = document.createElement('button');
      el.className = btn.class || 'btn';
      el.textContent = btn.text;
      el.addEventListener('click', () => {
        closeModal();
        btn.action?.();
      });
      modalFooter.appendChild(el);
    }

    overlay.style.display = 'flex';

    // 关闭按钮
    document.getElementById('btn-close-modal').onclick = closeModal;

    // 清除旧的事件监听器
    if (_modalOverlayHandler) overlay.removeEventListener('click', _modalOverlayHandler);
    if (_modalEscHandler) document.removeEventListener('keydown', _modalEscHandler);

    // 点击 overlay 关闭
    _modalOverlayHandler = (e) => { if (e.target === overlay) closeModal(); };
    overlay.addEventListener('click', _modalOverlayHandler);

    // ESC 关闭
    _modalEscHandler = (e) => { if (e.key === 'Escape') closeModal(); };
    document.addEventListener('keydown', _modalEscHandler);
  }

  function closeModal() {
    document.getElementById('modal-overlay').style.display = 'none';
    // 移除宽模态样式
    document.getElementById('modal').classList.remove('modal-wide');
    // 清理事件监听器
    if (_modalEscHandler) {
      document.removeEventListener('keydown', _modalEscHandler);
      _modalEscHandler = null;
    }
    if (_modalOverlayHandler) {
      document.getElementById('modal-overlay').removeEventListener('click', _modalOverlayHandler);
      _modalOverlayHandler = null;
    }
  }

  // ===== System Prompt Editor =====

  let _versionDiffA = null;
  let _versionDiffB = null;

  async function openPromptEditor() {
    const overlay = document.getElementById('modal-overlay');
    const modalEl = document.getElementById('modal');
    const modalTitle = document.getElementById('modal-title');
    const modalBody = document.getElementById('modal-body');
    const modalFooter = document.getElementById('modal-footer');

    modalTitle.textContent = 'System Prompt 设置';
    modalBody.innerHTML = `
      <div class="prompt-editor-layout">
        <div class="prompt-editor-main">
          <div class="prompt-editor-hint">
            自定义 Agent 的行为指令。修改后保存，下次对话将使用新的 prompt。
          </div>
          <div class="prompt-editor-status" id="prompt-status">加载中...</div>
          <textarea
            id="prompt-editor-textarea"
            class="prompt-editor-textarea"
            spellcheck="false"
            placeholder="输入 system prompt..."
          ></textarea>
          <div class="prompt-editor-info">
            <span id="prompt-char-count">0 字符</span>
            <span id="prompt-default-badge" class="prompt-badge" style="display:none;">默认</span>
          </div>
        </div>
        <div class="prompt-version-panel" id="prompt-version-panel">
          <div class="pv-header">
            <span class="pv-title">版本历史</span>
            <button class="btn btn-icon btn-sm" id="pv-btn-toggle" title="收起版本面板">◀</button>
          </div>
          <div class="pv-list" id="pv-list">
            <div class="pv-loading">加载中…</div>
          </div>
          <div class="pv-diff-area" id="pv-diff-area" style="display:none">
            <div class="pv-diff-header">
              <span>版本对比</span>
              <button class="btn btn-icon btn-sm" id="pv-diff-close" title="关闭对比">✕</button>
            </div>
            <div class="pv-diff-content" id="pv-diff-content"></div>
          </div>
        </div>
      </div>
    `;

    modalFooter.innerHTML = '';

    const resetBtn = document.createElement('button');
    resetBtn.className = 'btn btn-ghost';
    resetBtn.textContent = '恢复默认';
    resetBtn.addEventListener('click', async () => {
      if (!confirm('确定恢复为默认 System Prompt？')) return;
      try {
        const res = await API.resetSystemPrompt();
        document.getElementById('prompt-editor-textarea').value = res.prompt;
        updateCharCount(res.prompt);
        document.getElementById('prompt-default-badge').style.display = 'inline';
        document.getElementById('prompt-status').textContent = '已恢复默认';
        document.getElementById('prompt-status').className = 'prompt-editor-status success';
        toast('已恢复默认 prompt', 'success');
      } catch (e) {
        toast('重置失败: ' + e.message, 'error');
      }
    });
    modalFooter.appendChild(resetBtn);

    const saveBtn = document.createElement('button');
    saveBtn.className = 'btn btn-primary';
    saveBtn.textContent = '保存';
    saveBtn.addEventListener('click', async () => {
      const textarea = document.getElementById('prompt-editor-textarea');
      const text = textarea.value;
      if (!text.trim()) {
        toast('Prompt 不能为空', 'error');
        return;
      }
      try {
        saveBtn.disabled = true;
        saveBtn.textContent = '保存中...';
        await API.updateSystemPrompt(text);
        document.getElementById('prompt-status').textContent = '已保存，下次对话生效';
        document.getElementById('prompt-status').className = 'prompt-editor-status success';
        document.getElementById('prompt-default-badge').style.display = 'none';
        toast('System prompt 已保存', 'success');
        _loadVersionList();
      } catch (e) {
        toast('保存失败: ' + e.message, 'error');
      } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = '保存';
      }
    });
    modalFooter.appendChild(saveBtn);

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-ghost';
    cancelBtn.textContent = '关闭';
    cancelBtn.addEventListener('click', closeModal);
    modalFooter.appendChild(cancelBtn);

    modalEl.classList.add('modal-wide');
    overlay.style.display = 'flex';

    document.getElementById('btn-close-modal').onclick = closeModal;

    if (_modalOverlayHandler) overlay.removeEventListener('click', _modalOverlayHandler);
    if (_modalEscHandler) document.removeEventListener('keydown', _modalEscHandler);

    _modalOverlayHandler = (e) => { if (e.target === overlay) closeModal(); };
    overlay.addEventListener('click', _modalOverlayHandler);

    _modalEscHandler = (e) => { if (e.key === 'Escape') closeModal(); };
    document.addEventListener('keydown', _modalEscHandler);

    const textarea = document.getElementById('prompt-editor-textarea');
    textarea.addEventListener('input', () => updateCharCount(textarea.value));

    // 版本面板 toggle
    document.getElementById('pv-btn-toggle').addEventListener('click', () => {
      const panel = document.getElementById('prompt-version-panel');
      panel.classList.toggle('collapsed');
      document.getElementById('pv-btn-toggle').textContent = panel.classList.contains('collapsed') ? '▶' : '◀';
    });

    // diff 关闭
    document.getElementById('pv-diff-close').addEventListener('click', _closeDiffView);

    // 加载当前 prompt
    try {
      const data = await API.getSystemPrompt();
      textarea.value = data.prompt;
      updateCharCount(data.prompt);
      document.getElementById('prompt-status').textContent = data.is_default ? '当前使用默认 prompt' : '当前使用自定义 prompt';
      document.getElementById('prompt-status').className = 'prompt-editor-status';
      if (data.is_default) {
        document.getElementById('prompt-default-badge').style.display = 'inline';
      }
    } catch (e) {
      document.getElementById('prompt-status').textContent = '加载失败: ' + e.message;
      document.getElementById('prompt-status').className = 'prompt-editor-status error';
    }

    _loadVersionList();
  }

  async function _loadVersionList() {
    const listEl = document.getElementById('pv-list');
    if (!listEl) return;
    _versionDiffA = null;
    _versionDiffB = null;

    try {
      const versions = await API.listPromptVersions();
      if (versions.length === 0) {
        listEl.innerHTML = '<div class="pv-empty">暂无版本记录。保存 prompt 后会自动创建。</div>';
        return;
      }

      listEl.innerHTML = versions.slice().reverse().map(v => {
        const ts = new Date(v.timestamp);
        const timeStr = ts.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
        return `
          <div class="pv-item" data-id="${v.id}">
            <div class="pv-item-head">
              <span class="pv-item-label" title="双击编辑标签">${_escHtml(v.label || v.id)}</span>
              <span class="pv-item-time">${timeStr}</span>
            </div>
            ${v.note ? `<div class="pv-item-note">${_escHtml(v.note)}</div>` : ''}
            <div class="pv-item-meta">${v.char_count} 字符</div>
            <div class="pv-item-actions">
              <button class="btn btn-xs pv-btn-view" data-id="${v.id}" title="预览">👁</button>
              <button class="btn btn-xs pv-btn-rollback" data-id="${v.id}" title="回滚到此版本">↩</button>
              <button class="btn btn-xs pv-btn-diff" data-id="${v.id}" title="选中对比">⇔</button>
              <button class="btn btn-xs pv-btn-note" data-id="${v.id}" title="编辑备注">✏</button>
              <button class="btn btn-xs pv-btn-del" data-id="${v.id}" title="删除">🗑</button>
            </div>
          </div>
        `;
      }).join('');

      // 绑定事件
      listEl.querySelectorAll('.pv-btn-view').forEach(btn => {
        btn.addEventListener('click', () => _previewVersion(btn.dataset.id));
      });
      listEl.querySelectorAll('.pv-btn-rollback').forEach(btn => {
        btn.addEventListener('click', () => _rollbackVersion(btn.dataset.id));
      });
      listEl.querySelectorAll('.pv-btn-diff').forEach(btn => {
        btn.addEventListener('click', () => _selectDiffVersion(btn.dataset.id));
      });
      listEl.querySelectorAll('.pv-btn-note').forEach(btn => {
        btn.addEventListener('click', () => _editVersionNote(btn.dataset.id));
      });
      listEl.querySelectorAll('.pv-btn-del').forEach(btn => {
        btn.addEventListener('click', () => _deleteVersion(btn.dataset.id));
      });
    } catch (e) {
      listEl.innerHTML = `<div class="pv-empty">加载版本失败: ${e.message}</div>`;
    }
  }

  async function _previewVersion(id) {
    try {
      const v = await API.getPromptVersion(id);
      const textarea = document.getElementById('prompt-editor-textarea');
      textarea.value = v.content;
      updateCharCount(v.content);
      document.getElementById('prompt-status').textContent = `预览版本: ${v.label || v.id} (未保存)`;
      document.getElementById('prompt-status').className = 'prompt-editor-status';
    } catch (e) {
      toast('加载版本失败: ' + e.message, 'error');
    }
  }

  async function _rollbackVersion(id) {
    if (!confirm('确定回滚到此版本？当前 prompt 将被覆盖。')) return;
    try {
      const res = await API.rollbackPromptVersion(id);
      const textarea = document.getElementById('prompt-editor-textarea');
      textarea.value = res.prompt;
      updateCharCount(res.prompt);
      document.getElementById('prompt-status').textContent = '已回滚，下次对话生效';
      document.getElementById('prompt-status').className = 'prompt-editor-status success';
      toast('已回滚到选定版本', 'success');
      _loadVersionList();
    } catch (e) {
      toast('回滚失败: ' + e.message, 'error');
    }
  }

  async function _selectDiffVersion(id) {
    if (!_versionDiffA) {
      _versionDiffA = id;
      toast('已选中第 1 个版本，再点击一个版本进行对比', 'info');
      document.querySelectorAll(`.pv-btn-diff[data-id="${id}"]`).forEach(b => b.classList.add('selected'));
      return;
    }
    if (_versionDiffA === id) {
      _versionDiffA = null;
      document.querySelectorAll('.pv-btn-diff').forEach(b => b.classList.remove('selected'));
      return;
    }
    _versionDiffB = id;
    await _showVersionDiff(_versionDiffA, _versionDiffB);
    document.querySelectorAll('.pv-btn-diff').forEach(b => b.classList.remove('selected'));
    _versionDiffA = null;
    _versionDiffB = null;
  }

  async function _showVersionDiff(idA, idB) {
    try {
      const [vA, vB] = await Promise.all([API.getPromptVersion(idA), API.getPromptVersion(idB)]);
      const diffArea = document.getElementById('pv-diff-area');
      const diffContent = document.getElementById('pv-diff-content');
      diffArea.style.display = 'flex';

      const linesA = (vA.content || '').split('\n');
      const linesB = (vB.content || '').split('\n');
      const diff = _simpleDiff(linesA, linesB);

      diffContent.innerHTML = `
        <div class="pv-diff-labels">
          <span class="pv-diff-label-old">← ${_escHtml(vA.label || vA.id)}</span>
          <span class="pv-diff-label-new">→ ${_escHtml(vB.label || vB.id)}</span>
        </div>
        <div class="pv-diff-table">${diff}</div>
      `;
    } catch (e) {
      toast('加载对比失败: ' + e.message, 'error');
    }
  }

  function _closeDiffView() {
    document.getElementById('pv-diff-area').style.display = 'none';
  }

  function _simpleDiff(linesA, linesB) {
    const m = linesA.length, n = linesB.length;
    const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
    for (let i = 1; i <= m; i++)
      for (let j = 1; j <= n; j++)
        dp[i][j] = linesA[i - 1] === linesB[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);

    const entries = [];
    let i = m, j = n;
    while (i > 0 || j > 0) {
      if (i > 0 && j > 0 && linesA[i - 1] === linesB[j - 1]) {
        entries.push({ type: 'eq', text: linesA[i - 1] });
        i--; j--;
      } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
        entries.push({ type: 'add', text: linesB[j - 1] });
        j--;
      } else {
        entries.push({ type: 'del', text: linesA[i - 1] });
        i--;
      }
    }
    entries.reverse();

    return entries.map(e => {
      const cls = e.type === 'add' ? 'diff-add' : e.type === 'del' ? 'diff-del' : 'diff-eq';
      const sign = e.type === 'add' ? '+' : e.type === 'del' ? '-' : ' ';
      return `<div class="diff-line ${cls}"><span class="diff-sign">${sign}</span><span>${_escHtml(e.text)}</span></div>`;
    }).join('');
  }

  async function _editVersionNote(id) {
    const newNote = prompt('输入备注：');
    if (newNote === null) return;
    try {
      await API.updatePromptVersionMeta(id, undefined, newNote);
      _loadVersionList();
      toast('备注已更新', 'success');
    } catch (e) {
      toast('更新失败: ' + e.message, 'error');
    }
  }

  async function _deleteVersion(id) {
    if (!confirm('确定删除此版本？')) return;
    try {
      await API.deletePromptVersion(id);
      _loadVersionList();
      toast('版本已删除', 'success');
    } catch (e) {
      toast('删除失败: ' + e.message, 'error');
    }
  }

  function _escHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function updateCharCount(text) {
    const el = document.getElementById('prompt-char-count');
    if (el) el.textContent = `${text.length} 字符`;
  }

  // ===== Batch Runner =====

  let _batchPollTimer = null;

  async function openBatchRunner() {
    const overlay = document.getElementById('modal-overlay');
    const modalEl = document.getElementById('modal');
    const modalTitle = document.getElementById('modal-title');
    const modalBody = document.getElementById('modal-body');
    const modalFooter = document.getElementById('modal-footer');

    modalTitle.textContent = '批量运行';
    modalBody.innerHTML = `
      <div class="batch-runner">
        <!-- Existing tasks banner -->
        <div class="batch-task-list" id="batch-task-list" style="display:none"></div>

        <!-- Step 1: Config -->
        <div class="batch-step" id="batch-step-config">
          <div class="batch-upload-area" id="batch-upload-area">
            <div class="batch-upload-icon">📄</div>
            <div class="batch-upload-text">拖拽或点击上传 Excel 文件 (.xlsx)</div>
            <input type="file" id="batch-file-input" accept=".xlsx,.xls" style="display:none" />
          </div>
          <div class="batch-preview" id="batch-preview" style="display:none">
            <div class="batch-file-info" id="batch-file-info"></div>
          </div>
          <div class="batch-config-form" id="batch-config-form" style="display:none">
            <div class="batch-form-row">
              <label>Query 列 <input type="text" id="batch-query-col" value="B" class="batch-input-sm" /></label>
              <label>起始行 <input type="number" id="batch-start-row" value="2" class="batch-input-sm" /></label>
              <label>结束行 <input type="number" id="batch-end-row" value="100" class="batch-input-sm" /></label>
            </div>
            <div class="batch-form-row">
              <label>Content 写入列 <input type="text" id="batch-content-col" value="F" class="batch-input-sm" /></label>
              <label>Tool Calls 写入列 <input type="text" id="batch-tool-col" value="G" class="batch-input-sm" /></label>
            </div>
            <div class="batch-form-row">
              <label>Sheet
                <select id="batch-sheet-select" class="batch-select"></select>
              </label>
              <label>模型
                <select id="batch-model-select" class="batch-select"></select>
              </label>
            </div>
            <div class="batch-form-row">
              <label>Prompt 版本
                <select id="batch-prompt-select" class="batch-select">
                  <option value="">当前版本</option>
                </select>
              </label>
            </div>
            <button class="btn btn-primary batch-start-btn" id="batch-start-btn">开始运行</button>
          </div>
        </div>

        <!-- Step 2: Running -->
        <div class="batch-step" id="batch-step-running" style="display:none">
          <div class="batch-progress-header">
            <div class="batch-progress-bar-wrap">
              <div class="batch-progress-bar" id="batch-progress-bar" style="width:0%"></div>
            </div>
            <div class="batch-progress-text" id="batch-progress-text">准备中...</div>
          </div>
          <div class="batch-current-query" id="batch-current-query"></div>
          <div class="batch-results-table-wrap">
            <table class="batch-results-table">
              <thead><tr><th>行</th><th>Query</th><th>Content</th><th>Tools</th><th>状态</th></tr></thead>
              <tbody id="batch-results-body"></tbody>
            </table>
          </div>
          <button class="btn btn-ghost batch-cancel-btn" id="batch-cancel-btn">取消任务</button>
        </div>

        <!-- Step 3: Done -->
        <div class="batch-step" id="batch-step-done" style="display:none">
          <div class="batch-done-summary" id="batch-done-summary"></div>
          <div class="batch-results-table-wrap">
            <table class="batch-results-table">
              <thead><tr><th>行</th><th>Query</th><th>Content</th><th>Tools</th><th>状态</th></tr></thead>
              <tbody id="batch-done-body"></tbody>
            </table>
          </div>
          <div class="batch-done-actions">
            <a class="btn btn-primary" id="batch-download-btn" href="#" download>下载 Excel</a>
            <button class="btn btn-ghost" id="batch-new-btn">新建任务</button>
          </div>
        </div>
      </div>
    `;

    modalFooter.innerHTML = '';
    const closeBtn = document.createElement('button');
    closeBtn.className = 'btn btn-ghost';
    closeBtn.textContent = '关闭';
    closeBtn.addEventListener('click', () => { _stopBatchPoll(); closeModal(); });
    modalFooter.appendChild(closeBtn);

    modalEl.classList.add('modal-wide');
    overlay.style.display = 'flex';
    document.getElementById('btn-close-modal').onclick = () => { _stopBatchPoll(); closeModal(); };

    if (_modalOverlayHandler) overlay.removeEventListener('click', _modalOverlayHandler);
    if (_modalEscHandler) document.removeEventListener('keydown', _modalEscHandler);
    _modalOverlayHandler = (e) => { if (e.target === overlay) { _stopBatchPoll(); closeModal(); } };
    overlay.addEventListener('click', _modalOverlayHandler);
    _modalEscHandler = (e) => { if (e.key === 'Escape') { _stopBatchPoll(); closeModal(); } };
    document.addEventListener('keydown', _modalEscHandler);

    // Upload area
    const uploadArea = document.getElementById('batch-upload-area');
    const fileInput = document.getElementById('batch-file-input');
    let _uploadedFilename = null;

    uploadArea.addEventListener('click', () => fileInput.click());
    uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('dragover'); });
    uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
    uploadArea.addEventListener('drop', (e) => {
      e.preventDefault();
      uploadArea.classList.remove('dragover');
      if (e.dataTransfer.files.length) _handleBatchFile(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener('change', () => { if (fileInput.files.length) _handleBatchFile(fileInput.files[0]); });

    async function _handleBatchFile(file) {
      uploadArea.innerHTML = '<div class="batch-upload-text">上传中...</div>';
      try {
        const info = await API.uploadBatchExcel(file);
        _uploadedFilename = info.filename;
        uploadArea.innerHTML = `<div class="batch-upload-text">✓ ${_escHtml(info.filename)}</div>`;
        uploadArea.classList.add('uploaded');

        const preview = document.getElementById('batch-preview');
        const configForm = document.getElementById('batch-config-form');
        preview.style.display = 'block';
        configForm.style.display = 'block';

        // Populate sheets
        const sheetSel = document.getElementById('batch-sheet-select');
        sheetSel.innerHTML = info.sheets.map(s =>
          `<option value="${_escHtml(s.name)}">${_escHtml(s.name)} (${s.row_count} 行)</option>`
        ).join('');

        // Auto-set end_row
        if (info.sheets.length > 0) {
          document.getElementById('batch-end-row').value = info.sheets[0].row_count || 100;
        }

        // File info
        const firstSheet = info.sheets[0];
        const headersText = firstSheet.headers.map((h, i) => {
          const col = String.fromCharCode(65 + i);
          return `${col}: ${h || '(空)'}`;
        }).join(' | ');
        document.getElementById('batch-file-info').textContent = `列: ${headersText}`;

      } catch (e) {
        uploadArea.innerHTML = `<div class="batch-upload-text">上传失败: ${e.message}</div>`;
      }
    }

    // Load models
    try {
      const modelsData = await API.getModels();
      const modelSel = document.getElementById('batch-model-select');
      modelSel.innerHTML = modelsData.models.map(m =>
        `<option value="${m.id}" ${m.id === modelsData.default ? 'selected' : ''}>${m.name}</option>`
      ).join('');
    } catch (e) { /* ignore */ }

    // Load prompt versions
    try {
      const versions = await API.listPromptVersions();
      const promptSel = document.getElementById('batch-prompt-select');
      versions.slice().reverse().forEach(v => {
        const opt = document.createElement('option');
        opt.value = v.id;
        opt.textContent = `${v.label} (${new Date(v.timestamp).toLocaleDateString('zh-CN')})`;
        promptSel.appendChild(opt);
      });
    } catch (e) { /* ignore */ }

    // Start button
    document.getElementById('batch-start-btn').addEventListener('click', async () => {
      if (!_uploadedFilename) { toast('请先上传 Excel 文件', 'error'); return; }

      const config = {
        filename: _uploadedFilename,
        query_col: document.getElementById('batch-query-col').value.trim() || 'B',
        start_row: parseInt(document.getElementById('batch-start-row').value) || 2,
        end_row: parseInt(document.getElementById('batch-end-row').value) || 100,
        content_col: document.getElementById('batch-content-col').value.trim() || 'F',
        tool_col: document.getElementById('batch-tool-col').value.trim() || 'G',
        model: document.getElementById('batch-model-select').value,
        prompt_version_id: document.getElementById('batch-prompt-select').value || null,
        sheet_name: document.getElementById('batch-sheet-select').value || null,
      };

      try {
        const res = await API.startBatchRun(config);
        _showBatchRunning(res.task_id, res.total);
      } catch (e) {
        toast('启动失败: ' + e.message, 'error');
      }
    });

    // "New task" button
    document.getElementById('batch-new-btn').addEventListener('click', () => {
      _stopBatchPoll();
      closeModal();
      openBatchRunner();
    });

    // Check for existing tasks
    _loadBatchTaskList();
  }

  async function _loadBatchTaskList() {
    try {
      const tasks = await API.listBatchTasks();
      const listEl = document.getElementById('batch-task-list');
      if (!listEl || tasks.length === 0) return;

      const running = tasks.filter(t => t.status === 'running');
      const queued = tasks.filter(t => t.status === 'queued');
      const recent = tasks.filter(t => t.status !== 'running' && t.status !== 'queued').slice(-5).reverse();

      if (running.length === 0 && queued.length === 0 && recent.length === 0) return;

      listEl.style.display = 'block';
      let html = '<div class="batch-tl-title">历史任务</div><div class="batch-tl-items">';

      for (const t of [...running, ...queued, ...recent]) {
        const ts = new Date(t.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
        const statusLabel = t.status === 'running' ? '运行中' : t.status === 'queued' ? '排队中' : t.status === 'completed' ? '已完成' : t.status === 'cancelled' ? '已取消' : '出错';
        html += `
          <div class="batch-tl-item" data-id="${t.id}">
            <span class="batch-tl-status batch-tl-${t.status}">${statusLabel}</span>
            <span class="batch-tl-progress">${t.completed}/${t.total}</span>
            <span class="batch-tl-time">${ts}</span>
            <button class="btn btn-xs batch-tl-view" data-id="${t.id}">查看</button>
          </div>
        `;
      }
      html += '</div>';
      listEl.innerHTML = html;

      listEl.querySelectorAll('.batch-tl-view').forEach(btn => {
        btn.addEventListener('click', () => _resumeBatchView(btn.dataset.id));
      });

      if (running.length > 0) {
        _resumeBatchView(running[0].id);
      } else if (queued.length > 0) {
        _resumeBatchView(queued[0].id);
      }
    } catch (e) { /* ignore */ }
  }

  async function _resumeBatchView(taskId) {
    try {
      const task = await API.getBatchTask(taskId);
      if (task.status === 'running' || task.status === 'queued') {
        _showBatchRunning(taskId, task.total);
        _updateBatchProgress(task);
      } else {
        document.getElementById('batch-step-config').style.display = 'none';
        _showBatchDone(task);
      }
    } catch (e) {
      toast('加载任务失败: ' + e.message, 'error');
    }
  }

  function _showBatchRunning(taskId, total) {
    document.getElementById('batch-step-config').style.display = 'none';
    document.getElementById('batch-step-running').style.display = 'block';
    document.getElementById('batch-step-done').style.display = 'none';
    document.getElementById('batch-progress-text').textContent = `0 / ${total}`;

    document.getElementById('batch-cancel-btn').addEventListener('click', async () => {
      try {
        await API.cancelBatchTask(taskId);
        toast('任务取消中...', 'info');
      } catch (e) {
        toast('取消失败: ' + e.message, 'error');
      }
    });

    _startBatchPoll(taskId);
  }

  function _startBatchPoll(taskId) {
    _stopBatchPoll();
    _batchPollTimer = setInterval(async () => {
      try {
        const task = await API.getBatchTask(taskId);
        _updateBatchProgress(task);
        if (task.status !== 'running' && task.status !== 'queued') {
          _stopBatchPoll();
          _showBatchDone(task);
        }
      } catch (e) { /* ignore */ }
    }, 3000);
  }

  function _stopBatchPoll() {
    if (_batchPollTimer) {
      clearInterval(_batchPollTimer);
      _batchPollTimer = null;
    }
  }

  function _updateBatchProgress(task) {
    const pct = task.total > 0 ? Math.round((task.completed / task.total) * 100) : 0;
    const bar = document.getElementById('batch-progress-bar');
    const text = document.getElementById('batch-progress-text');
    if (bar) bar.style.width = pct + '%';

    if (task.status === 'queued') {
      if (text) text.textContent = `排队中 — 等待前序任务完成 (0 / ${task.total})`;
    } else {
      if (text) text.textContent = `${task.completed} / ${task.total} (${pct}%)`;
    }

    const curQ = document.getElementById('batch-current-query');
    if (curQ) curQ.textContent = task.status === 'queued' ? '排队等待中...' : (task.current_query ? `当前: ${task.current_query}` : '');

    const tbody = document.getElementById('batch-results-body');
    if (tbody && task.results) {
      tbody.innerHTML = task.results.slice(-20).map(r => `
        <tr class="batch-row-${r.status}">
          <td>${r.row}</td>
          <td title="${_escHtml(r.query)}">${_escHtml((r.query || '').slice(0, 40))}</td>
          <td title="${_escHtml(r.content)}">${_escHtml((r.content || '').slice(0, 60))}</td>
          <td title="${_escHtml(r.tool_calls)}">${_escHtml((r.tool_calls || '').slice(0, 40))}</td>
          <td>${r.status === 'done' ? '✓' : r.status === 'error' ? '✗' : r.status === 'skipped' ? '—' : '...'}</td>
        </tr>
      `).join('');
    }
  }

  function _showBatchDone(task) {
    document.getElementById('batch-step-running').style.display = 'none';
    document.getElementById('batch-step-done').style.display = 'block';

    const doneCount = task.results.filter(r => r.status === 'done').length;
    const errCount = task.results.filter(r => r.status === 'error').length;
    const skipCount = task.results.filter(r => r.status === 'skipped').length;
    const statusLabel = task.status === 'completed' ? '已完成' : task.status === 'cancelled' ? '已取消' : '出错';

    document.getElementById('batch-done-summary').innerHTML = `
      <div class="batch-summary-status batch-summary-${task.status}">${statusLabel}</div>
      <div class="batch-summary-stats">
        成功: <strong>${doneCount}</strong> | 失败: <strong>${errCount}</strong> | 跳过: <strong>${skipCount}</strong> | 总计: <strong>${task.total}</strong>
      </div>
      ${task.error ? `<div class="batch-summary-error">${_escHtml(task.error)}</div>` : ''}
    `;

    const tbody = document.getElementById('batch-done-body');
    tbody.innerHTML = task.results.map(r => `
      <tr class="batch-row-${r.status}">
        <td>${r.row}</td>
        <td title="${_escHtml(r.query)}">${_escHtml((r.query || '').slice(0, 40))}</td>
        <td title="${_escHtml(r.content)}">${_escHtml((r.content || '').slice(0, 60))}</td>
        <td title="${_escHtml(r.tool_calls)}">${_escHtml((r.tool_calls || '').slice(0, 40))}</td>
        <td>${r.status === 'done' ? '✓' : r.status === 'error' ? '✗' : r.status === 'skipped' ? '—' : '?'}</td>
      </tr>
    `).join('');

    const dlBtn = document.getElementById('batch-download-btn');
    dlBtn.href = API.batchDownloadUrl(task.id);
  }

  // ===== Subagent Manager =====

  let _availableTools = [];

  async function openSubagentManager() {
    const overlay = document.getElementById('modal-overlay');
    const modalEl = document.getElementById('modal');
    const modalTitle = document.getElementById('modal-title');
    const modalBody = document.getElementById('modal-body');
    const modalFooter = document.getElementById('modal-footer');

    modalTitle.textContent = 'Subagent 管理';
    modalBody.innerHTML = `
      <div class="subagent-manager">
        <div class="sa-hint">
          Subagent 可以被主 Agent 委派执行复杂任务（如深度研究），结果以摘要形式返回，保持主对话上下文简洁。
          修改后会自动生效（下次对话使用）。
        </div>
        <div class="sa-list" id="sa-list"><div class="sa-loading">加载中...</div></div>
      </div>
    `;

    modalFooter.innerHTML = '';

    const addBtn = document.createElement('button');
    addBtn.className = 'btn btn-primary';
    addBtn.textContent = '+ 新建 Subagent';
    addBtn.addEventListener('click', () => _openSubagentEditor(null));
    modalFooter.appendChild(addBtn);

    const closeBtn = document.createElement('button');
    closeBtn.className = 'btn btn-ghost';
    closeBtn.textContent = '关闭';
    closeBtn.addEventListener('click', closeModal);
    modalFooter.appendChild(closeBtn);

    modalEl.classList.add('modal-wide');
    overlay.style.display = 'flex';
    document.getElementById('btn-close-modal').onclick = closeModal;

    if (_modalOverlayHandler) overlay.removeEventListener('click', _modalOverlayHandler);
    if (_modalEscHandler) document.removeEventListener('keydown', _modalEscHandler);
    _modalOverlayHandler = (e) => { if (e.target === overlay) closeModal(); };
    overlay.addEventListener('click', _modalOverlayHandler);
    _modalEscHandler = (e) => { if (e.key === 'Escape') closeModal(); };
    document.addEventListener('keydown', _modalEscHandler);

    await _loadSubagentList();
  }

  async function _loadSubagentList() {
    const listEl = document.getElementById('sa-list');
    if (!listEl) return;

    try {
      const data = await API.listSubagents();
      const subagents = data.subagents || [];
      _availableTools = data.available_tools || [];

      if (subagents.length === 0) {
        listEl.innerHTML = '<div class="sa-empty">暂无 Subagent。点击下方按钮创建。</div>';
        return;
      }

      listEl.innerHTML = subagents.map(sa => {
        const toolBadges = (sa.tools || []).map(t => `<span class="sa-tool-badge">${_escHtml(t)}</span>`).join('');
        const enabledClass = sa.enabled !== false ? '' : ' sa-disabled';
        return `
          <div class="sa-item${enabledClass}" data-id="${sa.id}">
            <div class="sa-item-header">
              <span class="sa-item-icon">🤖</span>
              <span class="sa-item-name">${_escHtml(sa.name)}</span>
              ${sa.builtin ? '<span class="sa-badge-builtin">内置</span>' : ''}
              ${sa.enabled === false ? '<span class="sa-badge-disabled">已禁用</span>' : ''}
              <span class="sa-item-model">${_escHtml(sa.model || '继承主模型')}</span>
            </div>
            <div class="sa-item-desc">${_escHtml(sa.description || '')}</div>
            <div class="sa-item-tools">${toolBadges || '<span class="sa-no-tools">无工具</span>'}</div>
            <div class="sa-item-actions">
              <button class="btn btn-xs sa-btn-edit" data-id="${sa.id}">编辑</button>
              <button class="btn btn-xs sa-btn-toggle" data-id="${sa.id}">${sa.enabled !== false ? '禁用' : '启用'}</button>
              <button class="btn btn-xs sa-btn-delete" data-id="${sa.id}">删除</button>
            </div>
          </div>
        `;
      }).join('');

      listEl.querySelectorAll('.sa-btn-edit').forEach(btn => {
        btn.addEventListener('click', () => _openSubagentEditor(btn.dataset.id));
      });
      listEl.querySelectorAll('.sa-btn-toggle').forEach(btn => {
        btn.addEventListener('click', async () => {
          const sa = subagents.find(s => s.id === btn.dataset.id);
          if (!sa) return;
          const newEnabled = sa.enabled === false;
          try {
            await API.updateSubagent(btn.dataset.id, { enabled: newEnabled });
            toast(newEnabled ? 'Subagent 已启用' : 'Subagent 已禁用', 'success');
            _loadSubagentList();
          } catch (e) { toast('操作失败: ' + e.message, 'error'); }
        });
      });
      listEl.querySelectorAll('.sa-btn-delete').forEach(btn => {
        btn.addEventListener('click', async () => {
          if (!confirm('确定删除此 Subagent？')) return;
          try {
            await API.deleteSubagent(btn.dataset.id);
            toast('Subagent 已删除', 'success');
            _loadSubagentList();
          } catch (e) { toast('删除失败: ' + e.message, 'error'); }
        });
      });
    } catch (e) {
      listEl.innerHTML = `<div class="sa-empty">加载失败: ${e.message}</div>`;
    }
  }

  async function _openSubagentEditor(subagentId) {
    let existing = null;
    if (subagentId) {
      try {
        existing = await API.getSubagent(subagentId);
      } catch (e) {
        toast('加载失败: ' + e.message, 'error');
        return;
      }
    }

    const isEdit = !!existing;
    const title = isEdit ? `编辑 Subagent: ${existing.name}` : '新建 Subagent';

    const toolCheckboxes = _availableTools.map(t => {
      const checked = existing && (existing.tools || []).includes(t) ? 'checked' : '';
      return `<label class="sa-tool-check"><input type="checkbox" value="${t}" ${checked}/> ${t}</label>`;
    }).join('');

    const bodyHtml = `
      <div class="sa-editor">
        <div class="sa-editor-row">
          <label>名称 <span class="sa-hint-sm">（英文、短横线）</span></label>
          <input type="text" id="sa-edit-name" value="${_escHtml(existing?.name || '')}" placeholder="deep-research" />
        </div>
        <div class="sa-editor-row">
          <label>描述 <span class="sa-hint-sm">（主 Agent 用来决定何时委派）</span></label>
          <textarea id="sa-edit-desc" rows="2" placeholder="深度研究助手，适合需要多步搜索分析的复杂问题...">${_escHtml(existing?.description || '')}</textarea>
        </div>
        <div class="sa-editor-row">
          <label>System Prompt</label>
          <textarea id="sa-edit-prompt" rows="8" placeholder="你是一个专业的研究助手...">${_escHtml(existing?.system_prompt || '')}</textarea>
        </div>
        <div class="sa-editor-row">
          <label>可用工具</label>
          <div class="sa-tool-grid">${toolCheckboxes}</div>
        </div>
        <div class="sa-editor-row">
          <label>模型 <span class="sa-hint-sm">（留空则继承主 Agent 模型）</span></label>
          <input type="text" id="sa-edit-model" value="${_escHtml(existing?.model || '')}" placeholder="留空继承主模型" />
        </div>
      </div>
    `;

    modal(title, bodyHtml, [
      {
        text: isEdit ? '保存修改' : '创建',
        class: 'btn btn-primary',
        action: async () => {
          const name = document.getElementById('sa-edit-name').value.trim();
          const desc = document.getElementById('sa-edit-desc').value.trim();
          const prompt = document.getElementById('sa-edit-prompt').value.trim();
          const model = document.getElementById('sa-edit-model').value.trim();
          const tools = [];
          document.querySelectorAll('.sa-tool-check input:checked').forEach(cb => tools.push(cb.value));

          if (!name || !desc || !prompt) {
            toast('名称、描述和 System Prompt 不能为空', 'error');
            return;
          }

          const config = { name, description: desc, system_prompt: prompt, tools };
          if (model) config.model = model;

          try {
            if (isEdit) {
              await API.updateSubagent(subagentId, config);
              toast('Subagent 已更新', 'success');
            } else {
              await API.addSubagent(config);
              toast('Subagent 已创建', 'success');
            }
          } catch (e) {
            toast('操作失败: ' + e.message, 'error');
          }
          openSubagentManager();
        },
      },
      { text: '取消', class: 'btn btn-ghost', action: () => openSubagentManager() },
    ]);
  }

  // ===== User Profile Editor =====

  async function checkProfileStatus() {
    try {
      const data = await API.getUserProfile();
      const profile = data.profile || {};
      const hasContent = Object.values(profile).some(v => typeof v === 'string' && v.trim());
      const btn = document.getElementById('btn-user-profile');
      if (btn) btn.classList.toggle('has-profile', hasContent);
    } catch (e) {
      // ignore
    }
  }

  const PROFILE_SECTIONS = [
    {
      key: 'portfolio',
      icon: '💼',
      label: '投资组合 / Portfolio',
      desc: '描述您当前的资产配置，例如：股票、债券、基金、结构化产品的大致比例和主要持仓',
      placeholder: '自由修改为您的实际持仓情况',
      defaultValue: '一、股票持仓（约 70%）\n港股（约 40%）：腾讯 0700、美团 3690、阿里 9988\n美股（约 30%）：NVDA, AAPL, TSLA\n\n二、FCN 结构化产品（约 20%）\n1. 挂钩腾讯 0700 FCN\n   - 名义金额：50 万港币\n   - 期限：6 个月（2025/01/15 - 2025/07/15）\n   - 票息：年化 18%，按月派息\n   - Knock-Out（敲出）：初始价格的 103%\n   - Knock-In（敲入）：初始价格的 70%\n   - 初始价格：420 HKD\n   - 观察方式：每日观察敲入，每月观察敲出\n\n2. 挂钩阿里 9988 FCN\n   - 名义金额：30 万港币\n   - 期限：3 个月（2025/02/01 - 2025/05/01）\n   - 票息：年化 22%，按月派息\n   - Knock-Out（敲出）：初始价格的 105%\n   - Knock-In（敲入）：初始价格的 65%\n   - 初始价格：88 HKD\n   - 观察方式：每日观察敲入，每月观察敲出\n\n三、现金及货币基金（约 10%）',
    },
    {
      key: 'risk_preference',
      icon: '⚖️',
      label: '风险偏好 / Risk Preference',
      desc: '您对投资风险的容忍程度和偏好',
      placeholder: '自由修改为您的风险偏好',
      defaultValue: '中等偏保守，能接受 10-15% 的短期回撤\n偏好有下行保护的结构化产品（如 FCN、ELN）\n不喜欢高杠杆和纯投机交易\n追求年化 8-12% 的稳健收益',
    },
    {
      key: 'investment_habits',
      icon: '📊',
      label: '投资习惯 / Investment Habits',
      desc: '您的投资风格、交易频率、关注的市场和板块',
      placeholder: '自由修改为您的投资习惯',
      defaultValue: '中长期持有为主，平均持仓周期 3-12 个月\n每周关注市场动态和研报，每月调仓一次\n重点关注：科技股、结构化产品、大中华区市场\n投资风格偏价值投资，关注基本面和估值',
    },
    {
      key: 'user_persona',
      icon: '👤',
      label: '用户画像 / User Persona',
      desc: '您的职业背景、投资经验、对 Agent 沟通风格的偏好',
      placeholder: '自由修改为您的个人背景',
      defaultValue: '私人银行客户，5年投资经验\n熟悉股票、基金、结构化产品（FCN/雪球/ELN），了解基本的衍生品概念\n希望 Agent 用专业但易懂的语言沟通，中英文混合 OK\n喜欢有数据支撑的分析，不喜欢空泛的建议',
    },
    {
      key: 'custom_notes',
      icon: '📝',
      label: '其他备注 / Custom Notes',
      desc: '其他需要 Agent 了解的个性化信息',
      placeholder: '自由添加其他个性化需求',
      defaultValue: '对 ESG 和绿色投资有兴趣\n关注大中华区和东南亚市场\n每月有固定资金（约 10-20 万港币）需要配置\n偏好中文回复，涉及专业术语时附上英文',
    },
  ];

  async function openProfileEditor() {
    const overlay = document.getElementById('modal-overlay');
    const modalEl = document.getElementById('modal');
    const modalTitle = document.getElementById('modal-title');
    const modalBody = document.getElementById('modal-body');
    const modalFooter = document.getElementById('modal-footer');

    modalTitle.textContent = '用户画像设置';

    let sectionsHtml = PROFILE_SECTIONS.map(s => `
      <div class="profile-section">
        <div class="profile-section-header">
          <span class="profile-section-icon">${s.icon}</span>
          <span class="profile-section-label">${s.label}</span>
        </div>
        <div class="profile-section-desc">${s.desc}</div>
        <textarea
          id="profile-${s.key}"
          placeholder="${s.placeholder}"
          spellcheck="false"
        ></textarea>
      </div>
    `).join('');

    modalBody.innerHTML = `
      <div class="profile-editor-container">
        <div class="profile-editor-hint">
          设置您的投资画像，Agent 将根据这些信息个性化回复内容、风格和推荐。保存后下次对话生效。
        </div>
        <div class="profile-editor-status" id="profile-status">加载中...</div>
        ${sectionsHtml}
      </div>
    `;

    modalFooter.innerHTML = '';

    // 恢复预填充按钮
    const resetBtn = document.createElement('button');
    resetBtn.className = 'btn btn-ghost';
    resetBtn.textContent = '恢复预填充';
    resetBtn.addEventListener('click', () => {
      if (!confirm('确定恢复为预填充内容？当前修改将丢失。')) return;
      PROFILE_SECTIONS.forEach(s => {
        const el = document.getElementById(`profile-${s.key}`);
        if (el) el.value = s.defaultValue || '';
      });
    });
    modalFooter.appendChild(resetBtn);

    // 保存按钮
    const saveBtn = document.createElement('button');
    saveBtn.className = 'btn btn-primary';
    saveBtn.textContent = '保存';
    saveBtn.addEventListener('click', async () => {
      const profile = {};
      PROFILE_SECTIONS.forEach(s => {
        const el = document.getElementById(`profile-${s.key}`);
        profile[s.key] = el ? el.value : '';
      });

      try {
        saveBtn.disabled = true;
        saveBtn.textContent = '保存中...';
        await API.updateUserProfile(profile);
        document.getElementById('profile-status').textContent = '已保存，下次对话将根据画像个性化回复';
        document.getElementById('profile-status').className = 'profile-editor-status success';
        checkProfileStatus();
        toast('用户画像已保存', 'success');
      } catch (e) {
        toast('保存失败: ' + e.message, 'error');
      } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = '保存';
      }
    });
    modalFooter.appendChild(saveBtn);

    // 关闭按钮
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-ghost';
    cancelBtn.textContent = '关闭';
    cancelBtn.addEventListener('click', closeModal);
    modalFooter.appendChild(cancelBtn);

    modalEl.classList.add('modal-wide');
    overlay.style.display = 'flex';
    document.getElementById('btn-close-modal').onclick = closeModal;

    if (_modalOverlayHandler) overlay.removeEventListener('click', _modalOverlayHandler);
    if (_modalEscHandler) document.removeEventListener('keydown', _modalEscHandler);

    _modalOverlayHandler = (e) => { if (e.target === overlay) closeModal(); };
    overlay.addEventListener('click', _modalOverlayHandler);

    _modalEscHandler = (e) => { if (e.key === 'Escape') closeModal(); };
    document.addEventListener('keydown', _modalEscHandler);

    // 加载现有画像，若无已保存数据则用预填充值
    try {
      const data = await API.getUserProfile();
      const profile = data.profile || {};
      const hasContent = Object.values(profile).some(v => typeof v === 'string' && v.trim());

      PROFILE_SECTIONS.forEach(s => {
        const el = document.getElementById(`profile-${s.key}`);
        if (!el) return;
        if (profile[s.key] && profile[s.key].trim()) {
          el.value = profile[s.key];
        } else {
          el.value = s.defaultValue || '';
        }
      });

      document.getElementById('profile-status').textContent = hasContent
        ? '当前已设置画像，修改后保存即可'
        : '已预填充示例画像，请根据您的实际情况修改后保存';
      document.getElementById('profile-status').className = 'profile-editor-status';
    } catch (e) {
      PROFILE_SECTIONS.forEach(s => {
        const el = document.getElementById(`profile-${s.key}`);
        if (el) el.value = s.defaultValue || '';
      });
      document.getElementById('profile-status').textContent = '加载失败，已显示预填充内容: ' + e.message;
      document.getElementById('profile-status').className = 'profile-editor-status error';
    }
  }

  return { init, toast, modal, closeModal };
})();

// 启动应用
document.addEventListener('DOMContentLoaded', App.init);
