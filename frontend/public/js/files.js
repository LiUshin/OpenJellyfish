/**
 * 文件浏览器模块 - 树状目录、查看/编辑/新建/删除、拖拽上传、拖拽移动
 * 新增：fp-viewer 模式（编辑 / diff），占据文件面板 50% 宽度
 */

const Files = (() => {
  let currentPath = '/';
  let currentEditPath = null;
  let draggedItemPath = null;
  let draggedItemName = null;

  // viewer state
  let _viewerMode = null; // 'edit' | 'diff' | 'review' | null
  let _reviewDecided = false;

  // DOM refs (cached after init)
  let $browser, $viewer, $viewerFilename, $viewerActions, $editor, $diffEl, $backBtn, $saveBtn;

  function _ensurePanelVisible() {
    const fp = document.getElementById('file-panel');
    if (!fp.classList.contains('hidden')) return;
    fp.classList.remove('hidden');
    const handle = document.getElementById('fp-resize-handle');
    if (handle) handle.classList.remove('hidden');
    const btn = document.getElementById('btn-toggle-files');
    if (btn) btn.classList.add('active');
  }

  function init() {
    $browser = document.getElementById('fp-browser');
    $viewer = document.getElementById('fp-viewer');
    $viewerFilename = document.getElementById('fp-viewer-filename');
    $viewerActions = document.getElementById('fp-viewer-actions');
    $editor = document.getElementById('fp-editor');
    $diffEl = document.getElementById('fp-diff');
    $backBtn = document.getElementById('fp-back-btn');
    $saveBtn = document.getElementById('fp-btn-save');

    document.getElementById('btn-refresh-files').addEventListener('click', () => loadFiles(currentPath));
    document.getElementById('btn-new-file').addEventListener('click', showNewFileModal);
    document.getElementById('btn-new-folder').addEventListener('click', showNewFolderModal);

    $backBtn.addEventListener('click', closeViewer);
    $saveBtn.addEventListener('click', saveCurrentFile);

    $editor.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        saveCurrentFile();
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        closeViewer();
      }
    });

    setupDropzone();
    loadFiles('/');
  }

  // ==================== Viewer Mode ====================

  function _showViewer(mode) {
    _viewerMode = mode;
    $viewer.style.display = 'flex';
    document.getElementById('file-panel').classList.add('viewer-active');

    if (mode === 'edit') {
      $editor.style.display = 'block';
      $diffEl.style.display = 'none';
      $viewerActions.style.display = '';
      $viewerActions.innerHTML = `<span class="editor-shortcut-hint">Ctrl+S</span><button class="btn btn-sm btn-primary" id="fp-btn-save">💾 保存</button>`;
      document.getElementById('fp-btn-save').addEventListener('click', saveCurrentFile);
    } else if (mode === 'review') {
      $editor.style.display = 'none';
      $diffEl.style.display = 'block';
      // actions populated by showFileReview
    } else {
      $editor.style.display = 'none';
      $diffEl.style.display = 'block';
      $viewerActions.style.display = 'none';
    }
  }

  function closeViewer() {
    _viewerMode = null;
    currentEditPath = null;
    $viewer.style.display = 'none';
    document.getElementById('file-panel').classList.remove('viewer-active');
    document.querySelectorAll('.file-item.active').forEach(el => el.classList.remove('active'));
  }

  /** Open a file for editing in the viewer panel */
  async function openFile(path, name) {
    if (!name) name = path.split('/').pop();
    try {
      const result = await API.readFile(path);
      if (result.binary) {
        App.toast('二进制文件，无法编辑', 'info');
        return;
      }
      currentEditPath = path;
      $viewerFilename.textContent = name;
      $editor.value = result.content;
      _showViewer('edit');
      $editor.focus();
      $editor.setSelectionRange(0, 0);

      document.querySelectorAll('.file-item').forEach(item => {
        item.classList.toggle('active', item.dataset.path === path);
      });

      _ensurePanelVisible();
    } catch (err) {
      App.toast('打开文件失败: ' + err.message, 'error');
    }
  }

  /**
   * Show a diff view in the file panel.
   * Fetches the full file, locates old_string, and renders a unified diff with context.
   */
  async function showDiff(filePath, oldString, newString) {
    const name = filePath.split('/').pop();
    $viewerFilename.textContent = name;
    _showViewer('diff');
    $diffEl.innerHTML = '<div class="diff-loading">加载文件上下文…</div>';
    _ensurePanelVisible();

    const CTX = 5;
    try {
      const { content } = await API.readFile(filePath);
      const fileLines = content.split('\n');
      const oldLines = oldString.split('\n');
      const newLines = newString.split('\n');

      const matchIdx = content.indexOf(oldString);
      let oStart = 1;
      if (matchIdx >= 0) {
        oStart = content.substring(0, matchIdx).split('\n').length;
      }

      const ctxBefore = [];
      for (let li = Math.max(0, oStart - 1 - CTX); li < oStart - 1; li++) {
        ctxBefore.push({ type: 'eq', text: fileLines[li] });
      }

      const changeDiff = _computeLineDiff(oldLines, newLines);

      const endIdx = oStart - 1 + oldLines.length;
      const ctxAfter = [];
      for (let li = endIdx; li < Math.min(fileLines.length, endIdx + CTX); li++) {
        ctxAfter.push({ type: 'eq', text: fileLines[li] });
      }

      const fullDiff = [...ctxBefore, ...changeDiff, ...ctxAfter];
      const firstLine = Math.max(1, oStart - ctxBefore.length);
      $diffEl.innerHTML = _diffTableHtml(fullDiff, firstLine, firstLine);
    } catch {
      const diff = _computeLineDiff(oldString.split('\n'), newString.split('\n'));
      $diffEl.innerHTML = _diffTableHtml(diff, 1, 1);
    }
  }

  /** Show new file content (write_file) as all-green diff */
  function showNewFileDiff(filePath, content) {
    const name = filePath.split('/').pop();
    $viewerFilename.textContent = name;
    _showViewer('diff');
    _ensurePanelVisible();

    const entries = content.split('\n').map(t => ({ type: 'add', text: t }));
    $diffEl.innerHTML = _diffTableHtml(entries, 1, 1);
  }

  /**
   * Open full file review with inline diff + accept/reject controls.
   * The entire file is rendered; the change area is highlighted in place.
   * @param {Object} opts
   * @param {string}   opts.filePath
   * @param {boolean}  opts.isWrite      - true for write_file, false for edit_file
   * @param {string}   opts.oldContent   - old_string (edit_file only)
   * @param {string}   opts.newContent   - new_string or content
   * @param {function} opts.onDecision   - callback(type:'approve'|'reject')
   */
  async function showFileReview({ filePath, isWrite, oldContent, newContent, onDecision }) {
    const name = filePath.split('/').pop();
    $viewerFilename.textContent = name;
    _showViewer('review');
    _reviewDecided = false;
    _ensurePanelVisible();

    $viewerActions.style.display = '';
    $viewerActions.innerHTML = `
      <button class="btn btn-sm btn-approve" id="fp-rv-approve">✅ 接受</button>
      <button class="btn btn-sm btn-reject" id="fp-rv-reject">❌ 拒绝</button>
    `;

    function decide(type) {
      if (_reviewDecided) return;
      _reviewDecided = true;
      $viewerActions.innerHTML = `<span class="fp-rv-badge fp-rv-badge-${type}">${type === 'approve' ? '✅ 已接受' : '❌ 已拒绝'}</span>`;
      if (onDecision) onDecision(type);
    }

    document.getElementById('fp-rv-approve').addEventListener('click', () => decide('approve'));
    document.getElementById('fp-rv-reject').addEventListener('click', () => decide('reject'));

    $diffEl.innerHTML = '<div class="diff-loading">加载文件…</div>';

    if (isWrite) {
      const entries = newContent.split('\n').map(t => ({ type: 'add', text: t }));
      $diffEl.innerHTML = _diffTableHtml(entries, 1, 1);
    } else {
      try {
        const { content } = await API.readFile(filePath);
        const fileLines = content.split('\n');
        const oldLines = oldContent.split('\n');
        const newLines = newContent.split('\n');

        const matchIdx = content.indexOf(oldContent);
        let oStart = 1;
        if (matchIdx >= 0) {
          oStart = content.substring(0, matchIdx).split('\n').length;
        }

        const before = [];
        for (let li = 0; li < oStart - 1; li++) {
          before.push({ type: 'eq', text: fileLines[li] });
        }

        const changeDiff = _computeLineDiff(oldLines, newLines);

        const endIdx = oStart - 1 + oldLines.length;
        const after = [];
        for (let li = endIdx; li < fileLines.length; li++) {
          after.push({ type: 'eq', text: fileLines[li] });
        }

        const fullDiff = [...before, ...changeDiff, ...after];
        $diffEl.innerHTML = _diffTableHtml(fullDiff, 1, 1);

        setTimeout(() => {
          const first = $diffEl.querySelector('.diff-del, .diff-add');
          if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }, 80);
      } catch {
        const diff = _computeLineDiff(oldContent.split('\n'), newContent.split('\n'));
        $diffEl.innerHTML = _diffTableHtml(diff, 1, 1);
      }
    }
  }

  /** Called externally when the approval card is decided, to sync the file panel state */
  function markReviewDecided() {
    if (_viewerMode === 'review' && !_reviewDecided) {
      _reviewDecided = true;
      $viewerActions.innerHTML = '<span class="fp-rv-badge">已决策</span>';
    }
  }

  // ==================== Diff Utilities (self-contained) ====================

  function _computeLineDiff(oldLines, newLines) {
    const m = oldLines.length, n = newLines.length;
    if (m > 500 || n > 500) {
      return [...oldLines.map(t => ({ type: 'del', text: t })),
              ...newLines.map(t => ({ type: 'add', text: t }))];
    }
    const dp = [];
    for (let i = 0; i <= m; i++) dp[i] = new Uint16Array(n + 1);
    for (let i = 1; i <= m; i++)
      for (let j = 1; j <= n; j++)
        dp[i][j] = oldLines[i - 1] === newLines[j - 1]
          ? dp[i - 1][j - 1] + 1
          : Math.max(dp[i - 1][j], dp[i][j - 1]);
    const res = [];
    let i = m, j = n;
    while (i > 0 || j > 0) {
      if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
        res.unshift({ type: 'eq', text: oldLines[i - 1] }); i--; j--;
      } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
        res.unshift({ type: 'add', text: newLines[j - 1] }); j--;
      } else {
        res.unshift({ type: 'del', text: oldLines[i - 1] }); i--;
      }
    }
    return res;
  }

  function _charHL(oldT, newT) {
    let p = 0;
    const mn = Math.min(oldT.length, newT.length);
    while (p < mn && oldT[p] === newT[p]) p++;
    let sO = oldT.length, sN = newT.length;
    while (sO > p && sN > p && oldT[sO - 1] === newT[sN - 1]) { sO--; sN--; }
    const e = escapeHtml;
    return {
      del: e(oldT.slice(0, p)) + (sO > p ? `<mark class="diff-cdel">${e(oldT.slice(p, sO))}</mark>` : '') + e(oldT.slice(sO)),
      add: e(newT.slice(0, p)) + (sN > p ? `<mark class="diff-cins">${e(newT.slice(p, sN))}</mark>` : '') + e(newT.slice(sN)),
    };
  }

  function _diffTableHtml(entries, oS, nS) {
    let oL = oS, nL = nS, rows = '', k = 0;
    while (k < entries.length) {
      const e = entries[k];
      if (e.type === 'eq') {
        rows += `<tr class="diff-line diff-ctx"><td class="diff-gutter">${oL}</td><td class="diff-gutter">${nL}</td><td class="diff-sign"> </td><td class="diff-text">${escapeHtml(e.text)}</td></tr>`;
        oL++; nL++; k++;
      } else if (e.type === 'del') {
        const ds = [], as = [];
        while (k < entries.length && entries[k].type === 'del') ds.push(entries[k++]);
        while (k < entries.length && entries[k].type === 'add') as.push(entries[k++]);
        const pr = Math.min(ds.length, as.length);
        for (let p = 0; p < pr; p++) {
          const h = _charHL(ds[p].text, as[p].text);
          rows += `<tr class="diff-line diff-del"><td class="diff-gutter">${oL}</td><td class="diff-gutter"></td><td class="diff-sign">−</td><td class="diff-text">${h.del}</td></tr>`;
          oL++;
          rows += `<tr class="diff-line diff-add"><td class="diff-gutter"></td><td class="diff-gutter">${nL}</td><td class="diff-sign">+</td><td class="diff-text">${h.add}</td></tr>`;
          nL++;
        }
        for (let p = pr; p < ds.length; p++) {
          rows += `<tr class="diff-line diff-del"><td class="diff-gutter">${oL}</td><td class="diff-gutter"></td><td class="diff-sign">−</td><td class="diff-text">${escapeHtml(ds[p].text)}</td></tr>`;
          oL++;
        }
        for (let p = pr; p < as.length; p++) {
          rows += `<tr class="diff-line diff-add"><td class="diff-gutter"></td><td class="diff-gutter">${nL}</td><td class="diff-sign">+</td><td class="diff-text">${escapeHtml(as[p].text)}</td></tr>`;
          nL++;
        }
      } else {
        rows += `<tr class="diff-line diff-add"><td class="diff-gutter"></td><td class="diff-gutter">${nL}</td><td class="diff-sign">+</td><td class="diff-text">${escapeHtml(e.text)}</td></tr>`;
        nL++; k++;
      }
    }
    return `<table class="diff-table">${rows}</table>`;
  }

  // ==================== Directory Browser ====================

  async function loadFiles(path = '/') {
    currentPath = path;
    updateBreadcrumb(path);
    try {
      const items = await API.listFiles(path);
      renderFileList(items);
    } catch (err) {
      App.toast('加载文件失败: ' + err.message, 'error');
    }
  }

  function renderFileList(items) {
    const list = document.getElementById('file-list');
    list.innerHTML = '';

    if (currentPath !== '/') {
      const parentPath = getParentPath(currentPath);
      const parent = document.createElement('div');
      parent.className = 'file-item drop-target';
      parent.dataset.dropPath = parentPath;
      parent.innerHTML = `<span class="file-icon">📁</span><span class="file-name">..</span>`;
      parent.addEventListener('click', () => loadFiles(parentPath));
      setupDropTarget(parent, parentPath);
      list.appendChild(parent);
    }

    if (items.length === 0 && currentPath === '/') {
      list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:12px;">空目录</div>';
      return;
    }

    const sorted = [...items].sort((a, b) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      return a.name.localeCompare(b.name);
    });

    for (const item of sorted) {
      const el = document.createElement('div');
      el.className = 'file-item';
      el.dataset.path = item.path;
      el.draggable = true;

      const icon = item.is_dir ? '📁' : getFileIcon(item.name);
      const size = item.is_dir ? '' : formatSize(item.size);

      el.innerHTML = `
        <span class="file-icon">${icon}</span>
        <span class="file-name">${escapeHtml(item.name)}</span>
        <span class="file-size">${size}</span>
        <div class="file-item-actions">
          <button class="file-action-btn rename" title="重命名">✏️</button>
          ${!item.is_dir ? '<button class="file-action-btn download" title="下载">⬇</button>' : ''}
          <button class="file-action-btn delete" title="删除">🗑</button>
        </div>
      `;

      el.addEventListener('dragstart', (e) => {
        draggedItemPath = item.path;
        draggedItemName = item.name;
        el.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', item.path);
      });
      el.addEventListener('dragend', () => {
        draggedItemPath = null;
        draggedItemName = null;
        el.classList.remove('dragging');
        list.querySelectorAll('.drag-over').forEach(x => x.classList.remove('drag-over'));
      });

      if (item.is_dir) {
        el.classList.add('drop-target');
        el.dataset.dropPath = item.path;
        setupDropTarget(el, item.path);
      }

      el.addEventListener('click', (e) => {
        if (e.target.classList.contains('delete')) { e.stopPropagation(); confirmDelete(item.path, item.name); return; }
        if (e.target.classList.contains('download')) { e.stopPropagation(); downloadFile(item.path); return; }
        if (e.target.classList.contains('rename')) { e.stopPropagation(); showRenameModal(item.path, item.name); return; }
        if (item.is_dir) { loadFiles(item.path); } else { openFile(item.path, item.name); }
      });

      list.appendChild(el);
    }
  }

  function setupDropTarget(el, targetFolderPath) {
    let dragEnterCount = 0;
    el.addEventListener('dragover', (e) => {
      e.preventDefault(); e.stopPropagation();
      if (draggedItemPath === targetFolderPath) return;
      if (draggedItemPath && targetFolderPath.startsWith(draggedItemPath + '/')) return;
      e.dataTransfer.dropEffect = 'move';
    });
    el.addEventListener('dragenter', (e) => {
      e.preventDefault(); e.stopPropagation(); dragEnterCount++;
      if (draggedItemPath === targetFolderPath) return;
      if (draggedItemPath && targetFolderPath.startsWith(draggedItemPath + '/')) return;
      el.classList.add('drag-over');
    });
    el.addEventListener('dragleave', (e) => {
      e.preventDefault(); e.stopPropagation(); dragEnterCount--;
      if (dragEnterCount <= 0) { dragEnterCount = 0; el.classList.remove('drag-over'); }
    });
    el.addEventListener('drop', async (e) => {
      dragEnterCount = 0; e.preventDefault(); e.stopPropagation();
      el.classList.remove('drag-over');
      const sourcePath = e.dataTransfer.getData('text/plain') || draggedItemPath;
      if (!sourcePath || sourcePath === targetFolderPath) return;
      if (targetFolderPath.startsWith(sourcePath + '/')) { App.toast('不能将文件夹移入自身子目录', 'error'); return; }
      try {
        await API.moveFile(sourcePath, targetFolderPath);
        App.toast(`已移动 ${sourcePath.split('/').pop()}`, 'success');
        loadFiles(currentPath);
      } catch (err) { App.toast('移动失败: ' + err.message, 'error'); }
    });
  }

  // ==================== File Operations ====================

  async function saveCurrentFile() {
    if (!currentEditPath) return;
    try {
      await API.writeFile(currentEditPath, $editor.value);
      App.toast('保存成功', 'success');
    } catch (err) { App.toast('保存失败: ' + err.message, 'error'); }
  }

  function confirmDelete(path, name) {
    App.modal('确认删除',
      `<p>确定要删除 <strong>${escapeHtml(name)}</strong> 吗？此操作不可撤销。</p>`,
      [
        { text: '取消', class: 'btn btn-ghost' },
        { text: '删除', class: 'btn btn-danger', action: async () => {
          try {
            await API.deleteFile(path);
            App.toast('已删除', 'success');
            if (currentEditPath === path) closeViewer();
            loadFiles(currentPath);
          } catch (err) { App.toast('删除失败: ' + err.message, 'error'); }
        }},
      ]
    );
  }

  function showNewFileModal() {
    App.modal('新建文件',
      `<input type="text" id="new-file-name" placeholder="文件名，例如 notes.md" autofocus />`,
      [
        { text: '取消', class: 'btn btn-ghost' },
        { text: '创建', class: 'btn btn-primary', action: async () => {
          const name = document.getElementById('new-file-name').value.trim();
          if (!name) { App.toast('请输入文件名', 'error'); return; }
          const path = currentPath === '/' ? `/${name}` : `${currentPath}/${name}`;
          try { await API.writeFile(path, ''); App.toast('文件已创建', 'success'); loadFiles(currentPath); }
          catch (err) { App.toast('创建失败: ' + err.message, 'error'); }
        }},
      ]
    );
    setTimeout(() => document.getElementById('new-file-name')?.focus(), 100);
  }

  function showNewFolderModal() {
    App.modal('新建文件夹',
      `<input type="text" id="new-folder-name" placeholder="文件夹名" autofocus />`,
      [
        { text: '取消', class: 'btn btn-ghost' },
        { text: '创建', class: 'btn btn-primary', action: async () => {
          const name = document.getElementById('new-folder-name').value.trim();
          if (!name) { App.toast('请输入文件夹名', 'error'); return; }
          const path = currentPath === '/' ? `/${name}/.gitkeep` : `${currentPath}/${name}/.gitkeep`;
          try { await API.writeFile(path, ''); App.toast('文件夹已创建', 'success'); loadFiles(currentPath); }
          catch (err) { App.toast('创建失败: ' + err.message, 'error'); }
        }},
      ]
    );
    setTimeout(() => document.getElementById('new-folder-name')?.focus(), 100);
  }

  function showRenameModal(path, oldName) {
    App.modal('重命名',
      `<input type="text" id="rename-input" value="${escapeHtml(oldName)}" autofocus />`,
      [
        { text: '取消', class: 'btn btn-ghost' },
        { text: '确定', class: 'btn btn-primary', action: async () => {
          const newName = document.getElementById('rename-input').value.trim();
          if (!newName) { App.toast('名称不能为空', 'error'); return; }
          if (newName === oldName) return;
          const parentDir = getParentPath(path);
          const newPath = parentDir === '/' ? `/${newName}` : `${parentDir}/${newName}`;
          try {
            await API.moveFile(path, newPath);
            App.toast(`已重命名为 ${newName}`, 'success');
            if (currentEditPath === path) { currentEditPath = newPath; $viewerFilename.textContent = newName; }
            loadFiles(currentPath);
          } catch (err) { App.toast('重命名失败: ' + err.message, 'error'); }
        }},
      ]
    );
    setTimeout(() => {
      const input = document.getElementById('rename-input');
      if (input) { input.focus(); const d = oldName.lastIndexOf('.'); d > 0 ? input.setSelectionRange(0, d) : input.select(); }
    }, 100);
  }

  async function downloadFile(path) {
    try {
      const res = await API.downloadFile(path);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url; a.download = path.split('/').pop(); a.click();
      URL.revokeObjectURL(url);
    } catch (err) { App.toast('下载失败: ' + err.message, 'error'); }
  }

  // ==================== Upload ====================

  function setupDropzone() {
    const zone = document.getElementById('upload-dropzone');
    const filePanel = document.getElementById('file-panel');

    zone.addEventListener('click', () => {
      const input = document.createElement('input');
      input.type = 'file'; input.multiple = true;
      input.addEventListener('change', () => { if (input.files.length > 0) uploadFiles(input.files); });
      input.click();
    });

    ['dragenter', 'dragover'].forEach(evt => {
      filePanel.addEventListener(evt, (e) => { e.preventDefault(); zone.classList.add('dragover'); });
    });
    ['dragleave', 'drop'].forEach(evt => {
      filePanel.addEventListener(evt, (e) => { e.preventDefault(); zone.classList.remove('dragover'); });
    });
    filePanel.addEventListener('drop', (e) => {
      e.preventDefault();
      if (e.dataTransfer.files.length > 0) uploadFiles(e.dataTransfer.files);
    });
  }

  async function uploadFiles(fileList) {
    try {
      const result = await API.uploadFiles(currentPath, fileList);
      App.toast(`已上传 ${result.files?.length || 0} 个文件`, 'success');
      loadFiles(currentPath);
    } catch (err) { App.toast('上传失败: ' + err.message, 'error'); }
  }

  // ==================== Helpers ====================

  function getParentPath(path) { const parts = path.split('/').filter(Boolean); parts.pop(); return '/' + parts.join('/'); }

  function updateBreadcrumb(path) {
    const bc = document.getElementById('file-breadcrumb');
    bc.innerHTML = '';
    const parts = path.split('/').filter(Boolean);
    let accumulated = '/';
    const root = document.createElement('span'); root.className = 'breadcrumb-item'; root.textContent = '/'; root.addEventListener('click', () => loadFiles('/')); bc.appendChild(root);
    for (const part of parts) {
      accumulated += part + '/';
      const sep = document.createElement('span'); sep.className = 'breadcrumb-sep'; sep.textContent = '›'; bc.appendChild(sep);
      const item = document.createElement('span'); item.className = 'breadcrumb-item'; item.textContent = part;
      const itemPath = accumulated.slice(0, -1);
      item.addEventListener('click', () => loadFiles(itemPath)); bc.appendChild(item);
    }
  }

  function refresh() { loadFiles(currentPath); }

  function getFileIcon(name) {
    const ext = name.split('.').pop().toLowerCase();
    const icons = { md:'📄',txt:'📄',json:'📋',csv:'📊',py:'🐍',js:'📜',ts:'📜',html:'🌐',css:'🎨',pdf:'📕',doc:'📘',docx:'📘',xls:'📗',xlsx:'📗',png:'🖼',jpg:'🖼',jpeg:'🖼',gif:'🖼',svg:'🖼',zip:'📦',tar:'📦',gz:'📦' };
    return icons[ext] || '📄';
  }

  function formatSize(bytes) {
    if (bytes === 0) return '';
    const units = ['B','KB','MB','GB'];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
    return bytes.toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
  }

  function escapeHtml(str) { const div = document.createElement('div'); div.textContent = str; return div.innerHTML; }

  return { init, loadFiles, refresh, openFile, showDiff, showNewFileDiff, showFileReview, markReviewDecided, closeViewer };
})();
