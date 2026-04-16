/**
 * API 客户端封装
 * 所有 API 请求通过此模块发出
 */

const API = (() => {
  const BASE = '/api';

  /** 获取存储的 token */
  function getToken() {
    return localStorage.getItem('token') || '';
  }

  /** 设置 token */
  function setToken(token) {
    localStorage.setItem('token', token);
  }

  /** 清除 token */
  function clearToken() {
    localStorage.removeItem('token');
  }

  /** 通用请求方法 */
  async function request(method, path, body = null, options = {}) {
    const headers = {
      'Authorization': `Bearer ${getToken()}`,
    };

    if (body && !(body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
    }

    const config = {
      method,
      headers,
      ...options,
    };

    if (body) {
      config.body = body instanceof FormData ? body : JSON.stringify(body);
    }

    const res = await fetch(`${BASE}${path}`, config);

    if (res.status === 401) {
      clearToken();
      window.location.reload();
      throw new Error('认证失败，请重新登录');
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: '请求失败' }));
      throw new Error(err.detail || '请求失败');
    }

    return res.json();
  }

  // ===== Auth =====
  async function register(username, password, regKey) {
    return request('POST', '/auth/register', { username, password, reg_key: regKey });
  }

  async function login(username, password) {
    return request('POST', '/auth/login', { username, password });
  }

  async function getMe() {
    return request('GET', '/auth/me');
  }

  // ===== Conversations =====
  async function listConversations() {
    return request('GET', '/conversations');
  }

  async function createConversation(title = '新对话') {
    return request('POST', '/conversations', { title });
  }

  async function getConversation(convId) {
    return request('GET', `/conversations/${convId}`);
  }

  async function deleteConversation(convId) {
    return request('DELETE', `/conversations/${convId}`);
  }

  // ===== Chat (SSE) =====
  let _currentAbortController = null;

  /** 取消正在进行的 SSE stream */
  function abortStream() {
    if (_currentAbortController) {
      _currentAbortController.abort();
      _currentAbortController = null;
    }
  }

  /**
   * SSE 流式聊天
   * @param {string} conversationId
   * @param {string} message
   * @param {object} callbacks - {onToken, onThinking, onToolCall, onToolCallChunk, onToolResult, onDone, onError, onInterrupt}
   * @param {object} [options] - {model: string}
   */
  function streamChat(conversationId, message, callbacks, options = {}) {
    const { onToken, onThinking, onToolCall, onToolCallChunk, onToolResult, onDone, onError, onInterrupt,
            onSubagentCall, onSubagentCallChunk, onSubagentStart, onSubagentToken, onSubagentThinking,
            onSubagentToolCall, onSubagentToolChunk, onSubagentToolResult, onSubagentEnd } = callbacks;

    // 取消之前的 stream
    abortStream();

    const controller = new AbortController();
    _currentAbortController = controller;

    const body = { conversation_id: conversationId, message };
    if (options.model) body.model = options.model;
    if (options.capabilities) body.capabilities = options.capabilities;
    if (options.plan_mode) body.plan_mode = true;

    fetch(`${BASE}/chat`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${getToken()}`,
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    }).then(async (res) => {
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: '请求失败' }));
        onError?.(err.detail || '请求失败');
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop(); // 保留不完整行

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const dataStr = line.slice(6).trim();
            if (!dataStr) continue;

            try {
              const data = JSON.parse(dataStr);

              switch (data.type) {
                case 'token':
                  onToken?.(data.content);
                  break;
                case 'thinking':
                  onThinking?.(data.content);
                  break;
                case 'interrupt':
                  onInterrupt?.(data.actions, data.configs);
                  _currentAbortController = null;
                  return;
                case 'tool_call':
                  onToolCall?.(data.name, data.args);
                  break;
                case 'tool_call_chunk':
                  onToolCallChunk?.(data.args_delta);
                  break;
                case 'tool_result':
                  onToolResult?.(data.name, data.content);
                  break;
                case 'subagent_call':
                  onSubagentCall?.(data.name, data.task);
                  break;
                case 'subagent_call_chunk':
                  onSubagentCallChunk?.(data.args_delta);
                  break;
                case 'subagent_start':
                  onSubagentStart?.(data.name);
                  break;
                case 'subagent_token':
                  onSubagentToken?.(data.content, data.agent);
                  break;
                case 'subagent_thinking':
                  onSubagentThinking?.(data.content, data.agent);
                  break;
                case 'subagent_tool_call':
                  onSubagentToolCall?.(data.name, data.args, data.agent);
                  break;
                case 'subagent_tool_chunk':
                  onSubagentToolChunk?.(data.args_delta);
                  break;
                case 'subagent_tool_result':
                  onSubagentToolResult?.(data.name, data.content, data.agent);
                  break;
                case 'subagent_end':
                  onSubagentEnd?.(data.name, data.result);
                  break;
                case 'done':
                  onDone?.();
                  _currentAbortController = null;
                  return;
                case 'error':
                  onError?.(data.content);
                  _currentAbortController = null;
                  return;
              }
            } catch (e) {
              // 忽略 JSON 解析错误
            }
          }
        }

        // 处理 buffer 中剩余数据
        if (buffer.startsWith('data: ')) {
          try {
            const data = JSON.parse(buffer.slice(6).trim());
            if (data.type === 'token') onToken?.(data.content);
          } catch (e) { /* ignore */ }
        }

        onDone?.();
      } catch (e) {
        if (e.name === 'AbortError') return; // 正常取消
        throw e;
      } finally {
        _currentAbortController = null;
      }
    }).catch((err) => {
      if (err.name === 'AbortError') return; // 正常取消
      onError?.(err.message);
    });
  }

  /**
   * 恢复被 interrupt 暂停的 agent（SSE 流式）
   * @param {string} conversationId
   * @param {Array} decisions - [{type:"approve"}, {type:"reject"}, {type:"edit", edited_action:{...}}]
   * @param {object} callbacks - same as streamChat
   * @param {object} [options]
   */
  function resumeChat(conversationId, decisions, callbacks, options = {}) {
    const { onToken, onThinking, onToolCall, onToolCallChunk, onToolResult, onDone, onError, onInterrupt,
            onSubagentCall, onSubagentCallChunk, onSubagentStart, onSubagentToken, onSubagentThinking,
            onSubagentToolCall, onSubagentToolChunk, onSubagentToolResult, onSubagentEnd } = callbacks;

    abortStream();
    const controller = new AbortController();
    _currentAbortController = controller;

    const body = { conversation_id: conversationId, decisions };
    if (options.model) body.model = options.model;
    if (options.capabilities) body.capabilities = options.capabilities;

    fetch(`${BASE}/chat/resume`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${getToken()}`,
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    }).then(async (res) => {
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: '请求失败' }));
        onError?.(err.detail || '请求失败');
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop();

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const dataStr = line.slice(6).trim();
            if (!dataStr) continue;

            try {
              const data = JSON.parse(dataStr);
              switch (data.type) {
                case 'token':       onToken?.(data.content); break;
                case 'thinking':    onThinking?.(data.content); break;
                case 'interrupt':   onInterrupt?.(data.actions, data.configs); _currentAbortController = null; return;
                case 'tool_call':   onToolCall?.(data.name, data.args); break;
                case 'tool_call_chunk': onToolCallChunk?.(data.args_delta); break;
                case 'tool_result': onToolResult?.(data.name, data.content); break;
                case 'subagent_call':       onSubagentCall?.(data.name, data.task); break;
                case 'subagent_call_chunk': onSubagentCallChunk?.(data.args_delta); break;
                case 'subagent_start':      onSubagentStart?.(data.name); break;
                case 'subagent_token':      onSubagentToken?.(data.content, data.agent); break;
                case 'subagent_thinking':   onSubagentThinking?.(data.content, data.agent); break;
                case 'subagent_tool_call':  onSubagentToolCall?.(data.name, data.args, data.agent); break;
                case 'subagent_tool_chunk': onSubagentToolChunk?.(data.args_delta); break;
                case 'subagent_tool_result':onSubagentToolResult?.(data.name, data.content, data.agent); break;
                case 'subagent_end':        onSubagentEnd?.(data.name, data.result); break;
                case 'done':        onDone?.(); _currentAbortController = null; return;
                case 'error':       onError?.(data.content); _currentAbortController = null; return;
              }
            } catch (e) { /* ignore parse error */ }
          }
        }
        onDone?.();
      } catch (e) {
        if (e.name === 'AbortError') return;
        throw e;
      } finally {
        _currentAbortController = null;
      }
    }).catch((err) => {
      if (err.name === 'AbortError') return;
      onError?.(err.message);
    });
  }

  // ===== Files =====
  async function listFiles(path = '/') {
    return request('GET', `/files?path=${encodeURIComponent(path)}`);
  }

  async function readFile(path) {
    return request('GET', `/files/read?path=${encodeURIComponent(path)}`);
  }

  async function writeFile(path, content) {
    return request('POST', '/files/write', { path, content });
  }

  async function editFile(path, oldString, newString) {
    return request('PUT', '/files/edit', { path, old_string: oldString, new_string: newString });
  }

  async function deleteFile(path) {
    return request('DELETE', `/files?path=${encodeURIComponent(path)}`);
  }

  async function moveFile(source, destination) {
    return request('POST', '/files/move', { source, destination });
  }

  async function uploadFiles(path, files) {
    const formData = new FormData();
    for (const file of files) {
      formData.append('files', file);
    }
    return request('POST', `/files/upload?path=${encodeURIComponent(path)}`, formData);
  }

  async function downloadFile(path) {
    const res = await fetch(`${BASE}/files/download?path=${encodeURIComponent(path)}`, {
      headers: { 'Authorization': `Bearer ${getToken()}` },
    });
    if (!res.ok) throw new Error('下载失败');
    return res;
  }

  // ===== System Prompt =====
  async function getSystemPrompt() {
    return request('GET', '/system-prompt');
  }

  async function updateSystemPrompt(prompt) {
    return request('PUT', '/system-prompt', { prompt });
  }

  async function resetSystemPrompt() {
    return request('DELETE', '/system-prompt');
  }

  // ===== System Prompt 版本控制 =====
  async function listPromptVersions() {
    return request('GET', '/system-prompt/versions');
  }

  async function savePromptVersion(content, label = '', note = '') {
    return request('POST', '/system-prompt/versions', { content, label, note });
  }

  async function getPromptVersion(versionId) {
    return request('GET', `/system-prompt/versions/${versionId}`);
  }

  async function updatePromptVersionMeta(versionId, label, note) {
    return request('PUT', `/system-prompt/versions/${versionId}`, { label, note });
  }

  async function deletePromptVersion(versionId) {
    return request('DELETE', `/system-prompt/versions/${versionId}`);
  }

  async function rollbackPromptVersion(versionId) {
    return request('POST', `/system-prompt/versions/${versionId}/rollback`);
  }

  // ===== User Profile =====
  async function getUserProfile() {
    return request('GET', '/user-profile');
  }

  async function updateUserProfile(profile) {
    return request('PUT', '/user-profile', profile);
  }

  // ===== Scripts =====
  async function runScript(scriptPath, args = null, inputData = null, timeout = 30) {
    return request('POST', '/scripts/run', {
      script_path: scriptPath,
      args,
      input_data: inputData,
      timeout,
    });
  }

  /**
   * 生成媒体文件内联 URL（用于 img/audio/video/iframe src）
   * 通过 token 查询参数认证，无需 Authorization header
   */
  function mediaUrl(path) {
    return `${BASE}/files/media?path=${encodeURIComponent(path)}&token=${encodeURIComponent(getToken())}`;
  }

  // ===== Batch Execution =====

  async function uploadBatchExcel(file) {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(`${BASE}/batch/upload`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${getToken()}` },
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || '上传失败');
    }
    return res.json();
  }

  async function startBatchRun(config) {
    return request('POST', '/batch/run', config);
  }

  async function listBatchTasks() {
    return request('GET', '/batch/tasks');
  }

  async function getBatchTask(taskId) {
    return request('GET', `/batch/tasks/${taskId}`);
  }

  async function cancelBatchTask(taskId) {
    return request('POST', `/batch/tasks/${taskId}/cancel`);
  }

  function batchDownloadUrl(taskId) {
    return `${BASE}/batch/tasks/${taskId}/download?token=${encodeURIComponent(getToken())}`;
  }

  // ===== Subagents =====

  async function listSubagents() {
    return request('GET', '/subagents');
  }

  async function addSubagent(config) {
    return request('POST', '/subagents', config);
  }

  async function getSubagent(id) {
    return request('GET', `/subagents/${id}`);
  }

  async function updateSubagent(id, updates) {
    return request('PUT', `/subagents/${id}`, updates);
  }

  async function deleteSubagent(id) {
    return request('DELETE', `/subagents/${id}`);
  }

  // ===== Models =====

  async function getModels() {
    return request('GET', '/models');
  }

  /**
   * 语音转文字 — 上传音频文件到后端，调用 Whisper API
   * @param {Blob} audioBlob - 录音数据
   * @param {string} [filename='recording.webm'] - 文件名
   * @returns {Promise<{text: string}>}
   */
  async function transcribeAudio(audioBlob, filename = 'recording.webm') {
    const formData = new FormData();
    formData.append('file', audioBlob, filename);

    const res = await fetch(`${BASE}/audio/transcribe`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${getToken()}` },
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || '语音识别失败');
    }
    return res.json();
  }

  return {
    request,
    getToken, setToken, clearToken,
    register, login, getMe,
    listConversations, createConversation, getConversation, deleteConversation,
    streamChat, resumeChat, abortStream,
    getSystemPrompt, updateSystemPrompt, resetSystemPrompt,
    listPromptVersions, savePromptVersion, getPromptVersion, updatePromptVersionMeta, deletePromptVersion, rollbackPromptVersion,
    getUserProfile, updateUserProfile,
    listFiles, readFile, writeFile, editFile, deleteFile, moveFile, uploadFiles, downloadFile,
    runScript,
    mediaUrl,
    transcribeAudio,
    getModels,
    listSubagents, addSubagent, getSubagent, updateSubagent, deleteSubagent,
    uploadBatchExcel, startBatchRun, listBatchTasks, getBatchTask, cancelBatchTask, batchDownloadUrl,
  };
})();
