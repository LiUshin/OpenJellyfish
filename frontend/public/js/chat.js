/**
 * 聊天模块 - SSE streaming, markdown 渲染, 对话管理
 */

const Chat = (() => {
  let currentConvId = null;
  let isStreaming = false;
  let _selectedModel = '';  // 用户选择的模型 ID
  let _pendingImages = [];  // base64 data URLs for attached images

  // 配置 marked
  function initMarked() {
    if (typeof marked !== 'undefined') {
      marked.setOptions({
        highlight: function(code, lang) {
          if (typeof hljs !== 'undefined' && lang && hljs.getLanguage(lang)) {
            return hljs.highlight(code, { language: lang }).value;
          }
          return code;
        },
        breaks: true,
        gfm: true,
      });
    }
  }

  // ==================== 语音输入（按住说话） ====================

  let _mediaRecorder = null;
  let _audioChunks = [];
  let _voiceTimerInterval = null;
  let _voiceStartTime = 0;
  let _voiceStream = null;
  let _voiceHolding = false;

  function initVoice() {
    const btn = document.getElementById('btn-voice');
    if (!btn) return;

    btn.addEventListener('mousedown', onVoiceDown);
    window.addEventListener('mouseup', onVoiceUp);
    btn.addEventListener('touchstart', onVoiceDown, { passive: false });
    window.addEventListener('touchend', onVoiceUp, { passive: false });
    window.addEventListener('touchcancel', onVoiceUp, { passive: false });

    btn.addEventListener('contextmenu', (e) => e.preventDefault());

    window.addEventListener('keydown', (e) => {
      if (e.key !== 'Tab') return;
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) && e.target.id !== 'chat-input') return;
      e.preventDefault();
      if (e.repeat) return;
      const voiceBtn = document.getElementById('btn-voice');
      if (voiceBtn) voiceBtn.focus();
      onVoiceDown(e);
    });
    window.addEventListener('keyup', (e) => {
      if (e.key !== 'Tab') return;
      e.preventDefault();
      onVoiceUp(e);
    });
  }

  function onVoiceDown(e) {
    e.preventDefault();
    if (_voiceHolding) return;
    if (isStreaming) return;

    _voiceHolding = true;

    const btn = document.getElementById('btn-voice');
    const statusBar = document.getElementById('voice-status');
    const timerEl = document.getElementById('voice-timer');
    const hintEl = statusBar?.querySelector('.voice-hint');

    btn.classList.add('recording');
    statusBar.style.display = 'flex';
    timerEl.textContent = '0:00';
    if (hintEl) hintEl.textContent = '松开发送';
    _voiceStartTime = Date.now();

    _voiceTimerInterval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - _voiceStartTime) / 1000);
      const min = Math.floor(elapsed / 60);
      const sec = elapsed % 60;
      timerEl.textContent = `${min}:${sec.toString().padStart(2, '0')}`;
    }, 200);

    startRecording();
  }

  function onVoiceUp(e) {
    if (!_voiceHolding) return;
    _voiceHolding = false;

    if (_mediaRecorder && _mediaRecorder.state === 'recording') {
      _mediaRecorder.stop();
    } else {
      resetVoiceUI();
    }
  }

  async function startRecording() {
    try {
      _voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });

      if (!_voiceHolding) {
        _voiceStream.getTracks().forEach(t => t.stop());
        _voiceStream = null;
        resetVoiceUI();
        return;
      }

      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/mp4')
          ? 'audio/mp4'
          : '';

      _audioChunks = [];
      _mediaRecorder = new MediaRecorder(_voiceStream, mimeType ? { mimeType } : {});

      _mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) _audioChunks.push(e.data);
      };

      _mediaRecorder.onstop = async () => {
        _voiceStream?.getTracks().forEach(t => t.stop());
        _voiceStream = null;
        clearInterval(_voiceTimerInterval);

        if (_audioChunks.length === 0) {
          resetVoiceUI();
          return;
        }

        const blob = new Blob(_audioChunks, { type: _mediaRecorder.mimeType || 'audio/webm' });
        _audioChunks = [];

        if (Date.now() - _voiceStartTime < 500) {
          resetVoiceUI();
          App.toast('录音太短，请按住久一点', 'warning');
          return;
        }

        await transcribeAndSend(blob);
      };

      _mediaRecorder.start(250);

    } catch (err) {
      _voiceHolding = false;
      resetVoiceUI();
      if (err.name === 'NotAllowedError') {
        App.toast('麦克风权限被拒绝，请在浏览器设置中允许', 'error');
      } else {
        App.toast('无法访问麦克风: ' + err.message, 'error');
      }
    }
  }

  async function transcribeAndSend(audioBlob) {
    const btn = document.getElementById('btn-voice');
    const statusBar = document.getElementById('voice-status');
    const hintEl = statusBar?.querySelector('.voice-hint');

    btn.classList.remove('recording');
    btn.classList.add('transcribing');
    if (hintEl) hintEl.textContent = '识别中...';
    statusBar.querySelector('.voice-pulse').style.background = 'var(--accent)';

    try {
      const ext = audioBlob.type.includes('mp4') ? 'mp4' : 'webm';
      const result = await API.transcribeAudio(audioBlob, `recording.${ext}`);
      const text = (result.text || '').trim();

      if (!text) {
        App.toast('未识别到语音内容', 'warning');
        resetVoiceUI();
        return;
      }

      resetVoiceUI();

      const input = document.getElementById('chat-input');
      input.value = text;
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 150) + 'px';

      sendMessage();
    } catch (err) {
      App.toast('语音识别失败: ' + err.message, 'error');
      resetVoiceUI();
    }
  }

  function resetVoiceUI() {
    const btn = document.getElementById('btn-voice');
    const statusBar = document.getElementById('voice-status');
    if (btn) {
      btn.classList.remove('recording', 'transcribing');
    }
    if (statusBar) {
      statusBar.style.display = 'none';
      const hintEl = statusBar.querySelector('.voice-hint');
      if (hintEl) hintEl.textContent = '松开发送';
      statusBar.querySelector('.voice-pulse').style.background = 'var(--danger)';
    }
    clearInterval(_voiceTimerInterval);
  }

  // ==================== 空状态 & 推荐问题 ====================

  const EMPTY_STATE_HTML = `
    <div class="empty-state">
      <div class="empty-icon">💬</div>
      <p>开始对话吧</p>
      <p class="voice-hint-text">按住 <kbd>Tab</kbd> 说话，松开自动发送</p>
      <div class="suggestion-chips">
        <button class="suggestion-chip" data-msg="什么是FCN？帮我解释一下它的结构、收益和风险">📖 什么是 FCN</button>
        <button class="suggestion-chip" data-msg="FCN和雪球有什么区别？分别适合什么样的市场环境？">⚖️ FCN vs 雪球</button>
        <button class="suggestion-chip" data-msg="帮我根据最新的研报、新闻、股价推荐几个值得挂钩FCN的标的">📊 推荐 FCN 挂钩标的</button>
        <button class="suggestion-chip" data-msg="当前市场环境下，结构化产品应该怎么选？FCN、雪球、ELN各自的优劣是什么？">🧩 如何选择结构化产品</button>
      </div>
    </div>`;

  function bindSuggestionChips(container) {
    container.querySelectorAll('.suggestion-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const msg = chip.dataset.msg;
        if (!msg) return;
        const input = document.getElementById('chat-input');
        input.value = msg;
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 150) + 'px';
        sendMessage();
      });
    });
  }

  // ==================== 初始化 ====================

  function init() {
    initMarked();

    // 发送按钮
    document.getElementById('btn-send').addEventListener('click', sendMessage);

    // 回车发送
    const input = document.getElementById('chat-input');
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    // 自动调整高度
    input.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 150) + 'px';
    });

    // 新建对话
    document.getElementById('btn-new-chat').addEventListener('click', createNewChat);

    // 语音输入
    initVoice();

    // 图片附件
    _initImageAttach();

    // 绑定初始页面的推荐问题
    const chatMessages = document.getElementById('chat-messages');
    if (chatMessages) bindSuggestionChips(chatMessages);

    // 加载可用模型
    loadModels();
  }

  /** 加载可用模型列表并填充下拉框 */
  async function loadModels() {
    const select = document.getElementById('model-select');
    if (!select) return;

    try {
      const data = await API.getModels();
      const models = data.models || [];
      const defaultModel = data.default || '';

      select.innerHTML = '';

      if (models.length === 0) {
        select.innerHTML = '<option value="">无可用模型</option>';
        return;
      }

      // 按 provider 分组
      const providers = {};
      for (const m of models) {
        const p = m.provider || 'other';
        if (!providers[p]) providers[p] = [];
        providers[p].push(m);
      }

      const providerNames = {
        anthropic: 'Anthropic',
        openai: 'OpenAI',
        other: '其他',
      };

      for (const [provider, providerModels] of Object.entries(providers)) {
        const group = document.createElement('optgroup');
        group.label = providerNames[provider] || provider;
        for (const m of providerModels) {
          const opt = document.createElement('option');
          opt.value = m.id;
          opt.textContent = m.name;
          if (m.id === defaultModel) opt.selected = true;
          group.appendChild(opt);
        }
        select.appendChild(group);
      }

      _selectedModel = select.value || defaultModel;

      select.addEventListener('change', () => {
        _selectedModel = select.value;
      });

    } catch (err) {
      console.warn('加载模型列表失败:', err);
      select.innerHTML = '<option value="">加载失败</option>';
    }
  }

  /** 获取当前选中的 AI 能力列表 */
  function getSelectedCapabilities() {
    const caps = [];
    const checkboxes = document.querySelectorAll('#capability-toggles input[type="checkbox"]:checked');
    for (const cb of checkboxes) {
      caps.push(cb.value);
    }
    if (typeof HumanChat !== 'undefined' && HumanChat.isActive()) {
      caps.push('humanchat');
    }
    return caps.length > 0 ? caps : null;
  }

  /** Plan Mode 开关 */
  let _planModeActive = false;
  const planModeBtn = document.getElementById('btn-plan-mode');
  if (planModeBtn) {
    planModeBtn.addEventListener('click', () => {
      _planModeActive = !_planModeActive;
      planModeBtn.classList.toggle('active', _planModeActive);
      planModeBtn.title = _planModeActive ? 'Plan Mode 已开启' : 'Plan Mode：先规划再执行';
    });
  }

  /** 加载对话列表 */
  async function loadConversations() {
    try {
      const convs = await API.listConversations();
      renderConversationList(convs);
    } catch (err) {
      App.toast('加载对话失败: ' + err.message, 'error');
    }
  }

  function renderConversationList(convs) {
    const list = document.getElementById('conversation-list');
    list.innerHTML = '';

    if (convs.length === 0) {
      list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:12px;">暂无对话</div>';
      return;
    }

    for (const conv of convs) {
      const item = document.createElement('div');
      item.className = `conv-item${conv.id === currentConvId ? ' active' : ''}`;
      item.dataset.id = conv.id;
      item.innerHTML = `
        <span class="conv-item-title">${escapeHtml(conv.title)}</span>
        <button class="conv-item-delete" title="删除">&times;</button>
      `;

      item.addEventListener('click', (e) => {
        if (e.target.classList.contains('conv-item-delete')) {
          e.stopPropagation();
          deleteChat(conv.id);
          return;
        }
        selectConversation(conv.id, conv.title);
      });

      list.appendChild(item);
    }
  }

  /** 创建新对话 */
  async function createNewChat() {
    try {
      const conv = await API.createConversation();
      currentConvId = conv.id;
      await loadConversations();
      selectConversation(conv.id, conv.title);
    } catch (err) {
      App.toast('创建对话失败: ' + err.message, 'error');
    }
  }

  /** 选择对话 */
  async function selectConversation(convId, title) {
    // 切换对话时取消正在进行的 stream
    if (isStreaming) {
      API.abortStream();
      isStreaming = false;
      document.getElementById('btn-send').disabled = false;
    }

    currentConvId = convId;

    // 更新 UI
    document.getElementById('chat-title').textContent = title || '对话';
    document.getElementById('chat-input-area').style.display = 'block';
    const emptyState = document.getElementById('empty-state');
    if (emptyState) emptyState.style.display = 'none';

    // 高亮当前对话
    document.querySelectorAll('.conv-item').forEach(item => {
      item.classList.toggle('active', item.dataset.id === convId);
    });

    // 加载历史消息
    try {
      const conv = await API.getConversation(convId);
      renderMessages(conv.messages || []);
    } catch (err) {
      App.toast('加载对话失败: ' + err.message, 'error');
    }
  }

  /** 删除对话 */
  async function deleteChat(convId) {
    try {
      await API.deleteConversation(convId);
      if (currentConvId === convId) {
        currentConvId = null;
        document.getElementById('chat-title').textContent = '选择或创建一个对话';
        const msgContainer = document.getElementById('chat-messages');
        msgContainer.innerHTML = EMPTY_STATE_HTML;
        bindSuggestionChips(msgContainer);
        document.getElementById('chat-input-area').style.display = 'none';
      }
      await loadConversations();
      App.toast('对话已删除', 'success');
    } catch (err) {
      App.toast('删除失败: ' + err.message, 'error');
    }
  }

  /** 渲染历史消息 */
  function renderMessages(messages) {
    const container = document.getElementById('chat-messages');
    container.innerHTML = '';

    if (messages.length === 0) {
      container.innerHTML = EMPTY_STATE_HTML;
      bindSuggestionChips(container);
      return;
    }

    for (const msg of messages) {
      appendMessage(msg.role, msg.content, false, msg.tool_calls);
    }

    _userScrolledUp = false;
    scrollToBottom(true);
  }

  /** 添加一条消息到 UI */
  function appendMessage(role, content, scroll = true, toolCalls = null) {
    const container = document.getElementById('chat-messages');

    const empty = container.querySelector('.empty-state');
    if (empty) empty.remove();

    const div = document.createElement('div');
    div.className = `message message-${role}`;

    const avatarLetter = role === 'user' ? 'U' : 'A';
    const roleName = role === 'user' ? '你' : 'Agent';

    const rendered = renderMarkdown(content);

    div.innerHTML = `
      <div class="message-avatar">${avatarLetter}</div>
      <div class="message-body">
        <div class="message-role">${roleName}</div>
        <div class="message-content">${rendered}</div>
      </div>
    `;

    if (toolCalls && toolCalls.length > 0) {
      const bodyEl = div.querySelector('.message-body');
      const contentEl = bodyEl.querySelector('.message-content');
      const toolsHtml = _renderHistoryToolCalls(toolCalls);
      contentEl.insertAdjacentHTML('beforebegin', toolsHtml);

      bodyEl.querySelectorAll('.tool-indicator.done .tool-header').forEach(header => {
        header.addEventListener('click', () => {
          const indicator = header.parentElement;
          const preview = indicator.querySelector('.tool-stream-preview');
          if (preview) preview.classList.toggle('collapsed');
          const resultEl = indicator.querySelector('.tool-result-preview');
          if (resultEl) resultEl.classList.toggle('collapsed-result');
        });
      });
    }

    container.appendChild(div);
    if (scroll) scrollToBottom();

    return div;
  }

  function _renderHistoryToolCalls(toolCalls) {
    return toolCalls.map(tc => {
      const name = escapeHtml(tc.name || 'tool');
      const args = tc.args || '';
      const result = tc.result || '';
      const hasDetail = args || result;
      const detailHtml = hasDetail ? `
        <div class="tool-stream-preview collapsed">${escapeHtml(args)}</div>
        ${result ? `<div class="tool-result-preview collapsed-result">${escapeHtml(result)}</div>` : ''}
      ` : '';

      return `
        <div class="tool-indicator done" data-tool-name="${name}">
          <div class="tool-header" style="cursor:pointer">
            <span class="dot"></span>
            <span class="tool-name">🔧 ${name}</span>
            <span class="tool-status">✓ 完成</span>
          </div>
          ${detailHtml}
        </div>
      `;
    }).join('');
  }

  /**
   * 创建一个空的 streaming 消息容器
   * 返回 stream context 对象，用于按时间顺序动态追加文本和工具块
   */
  function createStreamingMessage() {
    const container = document.getElementById('chat-messages');

    const empty = container.querySelector('.empty-state');
    if (empty) empty.remove();

    const div = document.createElement('div');
    div.className = 'message message-assistant';
    div.innerHTML = `
      <div class="message-avatar">A</div>
      <div class="message-body">
        <div class="message-role">Agent</div>
      </div>
    `;

    const bodyEl = div.querySelector('.message-body');
    container.appendChild(div);
    scrollToBottom();

    // --- Stream context: 管理交替的文本段和工具块 ---
    const textSegments = [];        // [{el, text}] 所有文本段
    let currentContentEl = null;    // 当前活跃的文本块
    let currentSegmentText = '';    // 当前段的原始 markdown 文本

    // --- Thinking block state ---
    let thinkingEl = null;
    let thinkingText = '';

    // --- Subagent card state ---
    let _activeSubagentEl = null;
    let _subagentStreamText = '';
    let _subagentToolsInCard = [];
    let _subagentCallArgsBuf = '';

    /** 确保有一个活跃的文本块可以写入 */
    function ensureContentEl() {
      if (!currentContentEl) {
        currentContentEl = document.createElement('div');
        currentContentEl.className = 'message-content streaming-cursor';
        currentSegmentText = '';
        const segInfo = { el: currentContentEl, text: '' };
        textSegments.push(segInfo);
        bodyEl.appendChild(currentContentEl);
      }
    }

    /** 冻结当前文本块（停止光标），准备插入工具 */
    function freezeCurrentContent() {
      if (currentContentEl) {
        currentContentEl.classList.remove('streaming-cursor');
        // 保存当前段的文本
        const seg = textSegments[textSegments.length - 1];
        if (seg) seg.text = currentSegmentText;
        // 如果当前段是空的，移除它
        if (!currentSegmentText.trim()) {
          currentContentEl.remove();
          textSegments.pop();
        }
        currentContentEl = null;
        currentSegmentText = '';
      }
    }

    return {
      bodyEl,
      textSegments,

      /** 追加 thinking/reasoning 内容 */
      appendThinking(text) {
        if (!thinkingEl) {
          freezeCurrentContent();
          thinkingEl = document.createElement('div');
          thinkingEl.className = 'thinking-block';
          thinkingEl.innerHTML = `
            <div class="thinking-header">
              <span class="thinking-icon">💭</span>
              <span class="thinking-label">思考过程</span>
              <span class="thinking-toggle">▼</span>
            </div>
            <div class="thinking-content streaming-cursor"></div>
          `;
          const blockRef = thinkingEl;
          blockRef.querySelector('.thinking-header').addEventListener('click', () => {
            blockRef.classList.toggle('collapsed');
          });
          bodyEl.appendChild(thinkingEl);
        }
        thinkingText += text;
        const contentEl = thinkingEl.querySelector('.thinking-content');
        contentEl.textContent = thinkingText;
        scrollToBottom();
      },

      /** 追加文本 token（如果正在 thinking 则自动结束 thinking） */
      appendToken(token) {
        if (thinkingEl) {
          const contentEl = thinkingEl.querySelector('.thinking-content');
          contentEl.classList.remove('streaming-cursor');
          thinkingEl.classList.add('collapsed');
          thinkingEl = null;
          thinkingText = '';
        }
        ensureContentEl();
        currentSegmentText += token;
        // 更新当前段的渲染
        currentContentEl.innerHTML = renderMarkdown(currentSegmentText);
        const seg = textSegments[textSegments.length - 1];
        if (seg) seg.text = currentSegmentText;
        scrollToBottom();
      },

      /** 插入工具调用卡片 */
      addToolCall(name) {
        if (thinkingEl) {
          const contentEl = thinkingEl.querySelector('.thinking-content');
          contentEl.classList.remove('streaming-cursor');
          thinkingEl.classList.add('collapsed');
          thinkingEl = null;
          thinkingText = '';
        }
        freezeCurrentContent();

        const indicator = document.createElement('div');
        indicator.className = 'tool-indicator';
        indicator.dataset.toolName = name;

        const streamEl = document.createElement('div');
        streamEl.className = 'tool-stream-preview streaming-cursor';

        indicator.innerHTML = `
          <div class="tool-header">
            <span class="dot"></span>
            <span class="tool-name">🔧 ${escapeHtml(name)}</span>
            <span class="tool-status">生成中...</span>
          </div>
        `;
        indicator.appendChild(streamEl);
        bodyEl.appendChild(indicator);
        scrollToBottom();
      },

      /** 追加工具参数增量 */
      appendToolChunk(argsDelta) {
        const activeIndicator = bodyEl.querySelector('.tool-indicator:not(.done):last-child');
        if (!activeIndicator) return;

        const streamEl = activeIndicator.querySelector('.tool-stream-preview');
        if (!streamEl) return;

        streamEl.textContent += argsDelta;
        streamEl.scrollTop = streamEl.scrollHeight;
        scrollToBottom();
      },

      /** 标记工具完成 */
      completeToolResult(name, content) {
        const indicators = bodyEl.querySelectorAll('.tool-indicator:not(.done)');
        for (const indicator of indicators) {
          if (indicator.dataset.toolName === name || indicators.length === 1) {
            indicator.classList.add('done');

            const statusEl = indicator.querySelector('.tool-status');
            if (statusEl) statusEl.textContent = '✓ 完成';

            const streamEl = indicator.querySelector('.tool-stream-preview');
            if (streamEl) {
              streamEl.classList.remove('streaming-cursor');

              const hasContent = streamEl.textContent.trim().length > 0;
              if (hasContent) {
                streamEl.classList.add('collapsed');
                const header = indicator.querySelector('.tool-header');
                if (header) {
                  header.style.cursor = 'pointer';
                  header.addEventListener('click', () => {
                    streamEl.classList.toggle('collapsed');
                    scrollToBottom();
                  });
                }
              }
            }

            if (content && (!streamEl || !streamEl.textContent.includes(content.slice(0, 50)))) {
              const resultEl = document.createElement('div');
              resultEl.className = 'tool-result-preview';
              resultEl.textContent = content;

              // 长内容默认折叠，点击展开/收起
              if (content.length > 500) {
                resultEl.classList.add('collapsed-result');
                const toggleBtn = document.createElement('div');
                toggleBtn.className = 'tool-result-toggle';
                toggleBtn.textContent = '▼ 展开完整结果';
                toggleBtn.addEventListener('click', () => {
                  resultEl.classList.toggle('collapsed-result');
                  if (resultEl.classList.contains('collapsed-result')) {
                    toggleBtn.textContent = '▼ 展开完整结果';
                  } else {
                    toggleBtn.textContent = '▲ 收起';
                    scrollToBottom();
                  }
                });
                indicator.appendChild(toggleBtn);
              }
              indicator.appendChild(resultEl);
            }
            break;
          }
        }
        scrollToBottom();
      },

      /** 完成：最终渲染所有文本段 */
      finalize() {
        // 关闭未完成的 thinking block
        if (thinkingEl) {
          const contentEl = thinkingEl.querySelector('.thinking-content');
          contentEl.classList.remove('streaming-cursor');
          thinkingEl.classList.add('collapsed');
          thinkingEl = null;
          thinkingText = '';
        }

        // 保存最后一段的文本
        if (currentContentEl) {
          const seg = textSegments[textSegments.length - 1];
          if (seg) seg.text = currentSegmentText;
          currentContentEl.classList.remove('streaming-cursor');
        }

        // 对每个文本段做最终 markdown 渲染
        for (const seg of textSegments) {
          if (seg.text.trim()) {
            seg.el.innerHTML = renderMarkdown(seg.text);
          } else {
            seg.el.remove();
          }
        }

        currentContentEl = null;
        currentSegmentText = '';
      },

      /** 错误处理 */
      showError(msg) {
        ensureContentEl();
        currentContentEl.classList.remove('streaming-cursor');
        currentContentEl.innerHTML = `<span style="color:var(--danger)">错误: ${escapeHtml(msg)}</span>`;
      },

      /** 创建 subagent 卡片 — 初始 name/task 可能为空，后续通过 chunk 或 start 更新 */
      addSubagentCall(name, task) {
        if (thinkingEl) {
          const contentEl = thinkingEl.querySelector('.thinking-content');
          contentEl.classList.remove('streaming-cursor');
          thinkingEl.classList.add('collapsed');
          thinkingEl = null;
          thinkingText = '';
        }
        freezeCurrentContent();

        const card = document.createElement('div');
        card.className = 'subagent-card';
        card.dataset.agentName = name || '';

        card.innerHTML = `
          <div class="subagent-header">
            <span class="subagent-icon">🤖</span>
            <span class="subagent-name">${escapeHtml(name || 'Subagent')}</span>
            <span class="subagent-status">准备中...</span>
            <span class="subagent-toggle">▶</span>
          </div>
          <div class="subagent-task">${escapeHtml(task || '')}</div>
          <div class="subagent-stream collapsed">
            <div class="subagent-stream-content streaming-cursor"></div>
          </div>
        `;

        const header = card.querySelector('.subagent-header');
        const streamArea = card.querySelector('.subagent-stream');
        const toggleIcon = card.querySelector('.subagent-toggle');

        header.addEventListener('click', () => {
          streamArea.classList.toggle('collapsed');
          toggleIcon.textContent = streamArea.classList.contains('collapsed') ? '▶' : '▼';
          scrollToBottom();
        });

        bodyEl.appendChild(card);
        _activeSubagentEl = card;
        _subagentStreamText = '';
        _subagentToolsInCard = [];
        _subagentCallArgsBuf = '';
        scrollToBottom();
      },

      /** 接收 task() 工具参数的流式 chunk，增量解析并更新卡片
       *  deepagents 的 task() 参数格式: {"subagent_type": "...", "description": "..."}
       *  也兼容 {"name": "...", "task": "..."} 格式 */
      appendSubagentCallChunk(argsDelta) {
        if (!_activeSubagentEl) return;
        _subagentCallArgsBuf += argsDelta;

        const _applyName = (val) => {
          if (!val) return;
          const el = _activeSubagentEl.querySelector('.subagent-name');
          if (el) el.textContent = val;
          _activeSubagentEl.dataset.agentName = val;
        };
        const _applyTask = (val) => {
          if (!val) return;
          const el = _activeSubagentEl.querySelector('.subagent-task');
          if (el) el.textContent = val.replace(/\\n/g, '\n').replace(/\\"/g, '"');
        };

        try {
          const p = JSON.parse(_subagentCallArgsBuf);
          _applyName(p.subagent_type || p.name);
          _applyTask(p.description || p.task);
        } catch (_) {
          const nm = _subagentCallArgsBuf.match(/"(?:subagent_type|name)"\s*:\s*"([^"]+)"/);
          if (nm) _applyName(nm[1]);
          const tk = _subagentCallArgsBuf.match(/"(?:description|task)"\s*:\s*"((?:[^"\\]|\\.)*)"/);
          if (tk) _applyTask(tk[1]);
        }
      },

      /** subagent_start 事件：更新卡片名称和状态为"运行中" */
      updateSubagentStart(name) {
        if (!_activeSubagentEl) return;
        if (name) {
          const nameEl = _activeSubagentEl.querySelector('.subagent-name');
          if (nameEl) nameEl.textContent = name;
          _activeSubagentEl.dataset.agentName = name;
        }
        const statusEl = _activeSubagentEl.querySelector('.subagent-status');
        if (statusEl) statusEl.textContent = '运行中...';
      },

      /** 追加 subagent streaming token */
      appendSubagentToken(token) {
        if (!_activeSubagentEl) return;
        _subagentStreamText += token;
        const contentEl = _activeSubagentEl.querySelector('.subagent-stream-content');
        if (contentEl) {
          contentEl.innerHTML = renderMarkdown(_subagentStreamText);
          const streamArea = _activeSubagentEl.querySelector('.subagent-stream');
          if (streamArea && !streamArea.classList.contains('collapsed')) {
            scrollToBottom();
          }
        }
      },

      /** Subagent 内部工具调用 */
      addSubagentToolCall(toolName) {
        if (!_activeSubagentEl) return;
        const streamArea = _activeSubagentEl.querySelector('.subagent-stream');
        if (!streamArea) return;

        const toolEl = document.createElement('div');
        toolEl.className = 'subagent-tool';
        toolEl.dataset.toolName = toolName;
        toolEl.innerHTML = `
          <span class="subagent-tool-dot"></span>
          <span class="subagent-tool-name">🔧 ${escapeHtml(toolName)}</span>
          <span class="subagent-tool-status">调用中...</span>
        `;
        streamArea.insertBefore(toolEl, streamArea.querySelector('.subagent-stream-content'));
        _subagentToolsInCard.push(toolEl);
      },

      /** Subagent 工具完成 */
      completeSubagentTool(toolName) {
        if (!_activeSubagentEl) return;
        const toolEls = _activeSubagentEl.querySelectorAll('.subagent-tool:not(.done)');
        for (const el of toolEls) {
          if (el.dataset.toolName === toolName || toolEls.length === 1) {
            el.classList.add('done');
            const statusEl = el.querySelector('.subagent-tool-status');
            if (statusEl) statusEl.textContent = '✓';
            break;
          }
        }
      },

      /** 标记 subagent 完成 */
      completeSubagent(name, result) {
        if (!_activeSubagentEl) return;
        const card = _activeSubagentEl;
        card.classList.add('done');

        const statusEl = card.querySelector('.subagent-status');
        if (statusEl) {
          statusEl.textContent = '✓ 完成';
          statusEl.classList.add('done');
        }

        const streamContent = card.querySelector('.subagent-stream-content');
        if (streamContent) {
          streamContent.classList.remove('streaming-cursor');
          if (_subagentStreamText.trim()) {
            streamContent.innerHTML = renderMarkdown(_subagentStreamText);
          }
        }

        _activeSubagentEl = null;
        _subagentStreamText = '';
        _subagentToolsInCard = [];
        scrollToBottom();
      },

      /** 获取全部文本内容（用于保存） */
      getFullText() {
        return textSegments.map(s => s.text).join('');
      },
    };
  }

  /**
   * External entry point for HumanChat — sends a message
   * without reading from the main input field.
   */
  // ============ Image Attachment ============

  function _initImageAttach() {
    const input = document.getElementById('chat-input');
    const fileInput = document.getElementById('chat-img-input');
    const attachBtn = document.getElementById('btn-attach-img');

    if (attachBtn && fileInput) {
      attachBtn.addEventListener('click', () => fileInput.click());
      fileInput.addEventListener('change', () => {
        _addFiles(fileInput.files);
        fileInput.value = '';
      });
    }

    if (input) {
      input.addEventListener('paste', (e) => {
        const items = e.clipboardData?.items;
        if (!items) return;
        const images = [];
        for (const item of items) {
          if (item.type.startsWith('image/')) {
            images.push(item.getAsFile());
          }
        }
        if (images.length) {
          e.preventDefault();
          _addFiles(images);
        }
      });

      const wrapper = input.closest('.chat-input-area');
      if (wrapper) {
        wrapper.addEventListener('dragover', (e) => { e.preventDefault(); wrapper.classList.add('dragover'); });
        wrapper.addEventListener('dragleave', () => wrapper.classList.remove('dragover'));
        wrapper.addEventListener('drop', (e) => {
          e.preventDefault();
          wrapper.classList.remove('dragover');
          const files = [...e.dataTransfer.files].filter(f => f.type.startsWith('image/'));
          if (files.length) _addFiles(files);
        });
      }
    }
  }

  function _addFiles(files) {
    for (const file of files) {
      if (!file.type.startsWith('image/')) continue;
      if (_pendingImages.length >= 5) break;
      const reader = new FileReader();
      reader.onload = () => {
        _pendingImages.push({ dataUrl: reader.result, name: file.name });
        _renderImagePreview();
      };
      reader.readAsDataURL(file);
    }
  }

  function _renderImagePreview() {
    const container = document.getElementById('chat-img-preview');
    if (!container) return;
    if (!_pendingImages.length) {
      container.style.display = 'none';
      container.innerHTML = '';
      return;
    }
    container.style.display = 'flex';
    container.innerHTML = _pendingImages.map((img, i) =>
      `<div class="img-thumb">
        <img src="${img.dataUrl}" alt="${img.name || 'image'}" />
        <button class="img-remove" data-idx="${i}">&times;</button>
      </div>`
    ).join('');
    container.querySelectorAll('.img-remove').forEach(btn => {
      btn.addEventListener('click', () => {
        _pendingImages.splice(parseInt(btn.dataset.idx), 1);
        _renderImagePreview();
      });
    });
  }

  function _clearPendingImages() {
    _pendingImages = [];
    _renderImagePreview();
  }

  async function sendMessageFrom(text) {
    if (isStreaming || !text) return;
    await _doSendMessage(text);
  }

  /** 发送消息 (from main input) */
  async function sendMessage() {
    if (isStreaming) return;

    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text && !_pendingImages.length) return;

    input.value = '';
    input.style.height = 'auto';

    let message;
    if (_pendingImages.length) {
      message = [];
      if (text) message.push({ type: 'text', text });
      for (const img of _pendingImages) {
        message.push({ type: 'image_url', image_url: { url: img.dataUrl } });
      }
      _clearPendingImages();
    } else {
      message = text;
    }

    const displayText = text || '[图片]';
    await _doSendMessage(message, displayText);
  }

  /** 核心发送逻辑 — sendMessage 和 sendMessageFrom 共用 */
  async function _doSendMessage(message, displayText) {
    if (!displayText) displayText = typeof message === 'string' ? message : '[多模态消息]';
    if (!currentConvId) {
      try {
        const conv = await API.createConversation(displayText.slice(0, 30));
        currentConvId = conv.id;
        document.getElementById('chat-title').textContent = conv.title;
        document.getElementById('chat-input-area').style.display = 'block';
        await loadConversations();
      } catch (err) {
        App.toast('创建对话失败', 'error');
        return;
      }
    }

    _userScrolledUp = false;
    appendMessage('user', displayText);

    const _hcActive = typeof HumanChat !== 'undefined' && HumanChat.isActive();
    if (_hcActive) {
      HumanChat.addUserMessage(displayText);
      HumanChat.showTyping();
    }

    // 创建 streaming context
    const ctx = createStreamingMessage();

    isStreaming = true;
    document.getElementById('btn-send').disabled = true;

    const streamOpts = { model: _selectedModel, capabilities: getSelectedCapabilities(), plan_mode: _planModeActive };

    function makeCallbacks(streamCtx) {
      return {
        onThinking(content) { streamCtx.appendThinking(content); },
        onToken(token) { streamCtx.appendToken(token); },
        onToolCall(name, args) {
          streamCtx.addToolCall(name);
          if (name === 'send_message' && _hcActive) HumanChat.showTyping();
        },
        onToolCallChunk(argsDelta) { streamCtx.appendToolChunk(argsDelta); },
        onToolResult(name, content) {
          streamCtx.completeToolResult(name, content);
          if (name === 'send_message' && _hcActive) {
            try { HumanChat.addAgentMessage(JSON.parse(content)); }
            catch { HumanChat.addAgentMessage({ text: content }); }
          }
        },
        onSubagentCall(name, task) { streamCtx.addSubagentCall(name, task); },
        onSubagentCallChunk(argsDelta) { streamCtx.appendSubagentCallChunk(argsDelta); },
        onSubagentStart(name) { streamCtx.updateSubagentStart(name); },
        onSubagentToken(content) { streamCtx.appendSubagentToken(content); },
        onSubagentThinking(content) { streamCtx.appendSubagentToken(`💭 ${content}`); },
        onSubagentToolCall(name) { streamCtx.addSubagentToolCall(name); },
        onSubagentToolChunk() {},
        onSubagentToolResult(name) { streamCtx.completeSubagentTool(name); },
        onSubagentEnd(name, result) { streamCtx.completeSubagent(name, result); },
        onDone() {
          isStreaming = false;
          _userScrolledUp = false;
          document.getElementById('btn-send').disabled = false;
          streamCtx.finalize();
          if (_hcActive) HumanChat.hideTyping();
          loadConversations();
          if (typeof Files !== 'undefined') Files.refresh();
          scrollToBottom(true);
        },
        onError(msg) {
          isStreaming = false;
          _userScrolledUp = false;
          document.getElementById('btn-send').disabled = false;
          streamCtx.showError(msg);
          scrollToBottom(true);
          if (_hcActive) HumanChat.hideTyping();
        },
        onInterrupt(actions, configs) {
          ctx.finalize();
          _renderApprovalCard(ctx.bodyEl, actions, configs, streamOpts, _hcActive);
          scrollToBottom(true);
        },
      };
    }

    API.streamChat(currentConvId, message, makeCallbacks(ctx), streamOpts);
  }

  // ============ Diff Utilities ============

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

  // ============ HITL Approval Card ============

  function _renderApprovalCard(bodyEl, actions, configs, streamOpts, hcActive) {
    const configMap = {};
    for (const c of configs) configMap[c.action_name] = c;
    const DIFF_CTX = 3;
    const isBatch = actions.length > 1;

    /** Per-card state for batch mode. cardStates[i] = { checked, edited, editedAction } */
    const cardStates = actions.map(() => ({ checked: true, edited: false, editedAction: null }));

    function _doResume(decisions) {
      const resumeCtx = createStreamingMessage();
      isStreaming = true;
      document.getElementById('btn-send').disabled = true;

      const resumeCbs = {
        onThinking(content) { resumeCtx.appendThinking(content); },
        onToken(token) { resumeCtx.appendToken(token); },
        onToolCall(name) { resumeCtx.addToolCall(name); },
        onToolCallChunk(delta) { resumeCtx.appendToolChunk(delta); },
        onToolResult(name, content) {
          resumeCtx.completeToolResult(name, content);
          if (name === 'send_message' && hcActive) {
            try { HumanChat.addAgentMessage(JSON.parse(content)); }
            catch { HumanChat.addAgentMessage({ text: content }); }
          }
        },
        onSubagentCall(name, task) { resumeCtx.addSubagentCall(name, task); },
        onSubagentCallChunk(argsDelta) { resumeCtx.appendSubagentCallChunk(argsDelta); },
        onSubagentStart(name) { resumeCtx.updateSubagentStart(name); },
        onSubagentToken(content) { resumeCtx.appendSubagentToken(content); },
        onSubagentThinking(content) { resumeCtx.appendSubagentToken(`\u{1F4AD} ${content}`); },
        onSubagentToolCall(name) { resumeCtx.addSubagentToolCall(name); },
        onSubagentToolChunk() {},
        onSubagentToolResult(name) { resumeCtx.completeSubagentTool(name); },
        onSubagentEnd(name, result) { resumeCtx.completeSubagent(name, result); },
        onDone() {
          isStreaming = false;
          document.getElementById('btn-send').disabled = false;
          resumeCtx.finalize();
          if (hcActive) HumanChat.hideTyping();
          loadConversations();
          if (typeof Files !== 'undefined') Files.refresh();
          scrollToBottom(true);
        },
        onError(msg) {
          isStreaming = false;
          document.getElementById('btn-send').disabled = false;
          resumeCtx.showError(msg);
          scrollToBottom(true);
        },
        onInterrupt(newActions, newConfigs) {
          resumeCtx.finalize();
          _renderApprovalCard(resumeCtx.bodyEl, newActions, newConfigs, streamOpts, hcActive);
          scrollToBottom(true);
        },
      };

      API.resumeChat(currentConvId, decisions, resumeCbs, streamOpts);
    }

    // ---- Batch toolbar (rendered at end, updated live) ----
    let batchToolbar = null;
    const allCards = [];

    function _updateBatchSummary() {
      if (!batchToolbar) return;
      const selected = cardStates.filter(s => s.checked).length;
      const edited = cardStates.filter(s => s.edited).length;
      const total = actions.length;
      let label = `<strong>${selected}</strong> / ${total} 项已选中`;
      if (edited > 0) label += `，<strong>${edited}</strong> 项已编辑`;
      batchToolbar.querySelector('.batch-summary').innerHTML = label;
    }

    function _markAllDecided(batchDecided) {
      if (batchToolbar) batchToolbar.remove();
      for (let i = 0; i < allCards.length; i++) {
        const card = allCards[i];
        const type = batchDecided ? (cardStates[i].checked ? (cardStates[i].edited ? 'edit' : 'approve') : 'reject') : 'reject';
        card.querySelectorAll('.approval-actions').forEach(d => d.remove());
        card.querySelectorAll('.approval-card-select').forEach(d => d.remove());
        card.querySelectorAll('.plan-add-step').forEach(b => b.remove());
        card.querySelectorAll('.plan-step-del').forEach(b => b.remove());
        card.querySelectorAll('.plan-step-input').forEach(inp => { inp.readOnly = true; inp.classList.add('readonly'); });
        card.classList.add('decided', `decided-${type}`);
        const badge = document.createElement('div');
        badge.className = `approval-badge approval-badge-${type}`;
        badge.textContent = type === 'approve' ? '✅ 已批准' : type === 'reject' ? '❌ 已拒绝' : '✏️ 已编辑并批准';
        card.appendChild(badge);
      }
      if (typeof Files !== 'undefined') Files.markReviewDecided();
    }

    function _batchSubmit() {
      const decisions = actions.map((action, i) => {
        if (!cardStates[i].checked) return { type: 'reject' };
        if (cardStates[i].edited && cardStates[i].editedAction) return cardStates[i].editedAction;
        return { type: 'approve' };
      });
      _markAllDecided(true);
      _doResume(decisions);
    }

    function _batchRejectAll() {
      const decisions = actions.map(() => ({ type: 'reject' }));
      _markAllDecided(false);
      _doResume(decisions);
    }

    // ---- Render each card ----

    for (let i = 0; i < actions.length; i++) {
      const action = actions[i];
      const rc = configMap[action.name] || {};
      const allowed = rc.allowed_decisions || ['approve', 'reject'];

      // ============ propose_plan ============
      if (action.name === 'propose_plan') {
        const steps = action.args?.steps || [];
        const questions = action.args?.questions || [];

        const card = document.createElement('div');
        card.className = 'approval-card plan-approval-card';
        card.dataset.actionIndex = i;
        allCards.push(card);

        let stepsHtml = steps.map((s, idx) => `
          <div class="plan-step" data-idx="${idx}">
            <span class="plan-step-num">${idx + 1}.</span>
            <input type="text" class="plan-step-input" value="${escapeHtml(s)}" />
            <button class="plan-step-del" title="删除此步骤">&times;</button>
          </div>
        `).join('');

        let questionsHtml = '';
        if (questions.length > 0) {
          questionsHtml = `
            <div class="plan-questions">
              <div class="plan-questions-label">Agent 的补充问题：</div>
              ${questions.map((q) => `
                <div class="plan-question-item">
                  <span class="plan-q-text">${escapeHtml(q)}</span>
                </div>
              `).join('')}
            </div>
          `;
        }

        const checkboxHtml = isBatch
          ? `<div class="approval-card-select"><input type="checkbox" id="ap-chk-${i}" checked /><label for="ap-chk-${i}">批准</label></div>`
          : '';

        card.innerHTML = `
          <div class="approval-header plan-header">
            ${checkboxHtml}
            <span class="approval-icon">📋</span>
            <span class="approval-label">执行计划</span>
            <span class="plan-step-count">${steps.length} 个步骤</span>
          </div>
          <div class="plan-steps-container">
            ${stepsHtml}
          </div>
          <button class="btn btn-xs plan-add-step">+ 添加步骤</button>
          ${questionsHtml}
          ${!isBatch ? `<div class="approval-actions">
            ${allowed.includes('approve') ? '<button class="btn btn-sm btn-approve">✅ 批准执行</button>' : ''}
            ${allowed.includes('reject') ? '<button class="btn btn-sm btn-reject">❌ 拒绝</button>' : ''}
          </div>` : ''}
        `;
        bodyEl.appendChild(card);

        const stepsContainer = card.querySelector('.plan-steps-container');
        let _planDecided = false;

        function _renumberSteps() {
          const nums = stepsContainer.querySelectorAll('.plan-step-num');
          nums.forEach((el, idx) => { el.textContent = `${idx + 1}.`; });
          card.querySelector('.plan-step-count').textContent = `${nums.length} 个步骤`;
        }

        stepsContainer.addEventListener('click', (e) => {
          if (e.target.classList.contains('plan-step-del')) {
            e.target.closest('.plan-step').remove();
            _renumberSteps();
            _markPlanEdited();
          }
        });

        card.querySelector('.plan-add-step').addEventListener('click', () => {
          const newStep = document.createElement('div');
          newStep.className = 'plan-step';
          newStep.innerHTML = `
            <span class="plan-step-num">0.</span>
            <input type="text" class="plan-step-input" value="" placeholder="输入步骤描述..." />
            <button class="plan-step-del" title="删除此步骤">&times;</button>
          `;
          stepsContainer.appendChild(newStep);
          _renumberSteps();
          newStep.querySelector('.plan-step-input').focus();
        });

        function _collectSteps() {
          return Array.from(stepsContainer.querySelectorAll('.plan-step-input'))
            .map(inp => inp.value.trim())
            .filter(v => v);
        }

        function _markPlanEdited() {
          const finalSteps = _collectSteps();
          const changed = finalSteps.length !== steps.length || finalSteps.some((s, idx) => s !== steps[idx]);
          cardStates[i].edited = changed;
          if (changed) {
            cardStates[i].editedAction = { type: 'edit', edited_action: { name: 'propose_plan', args: { steps: finalSteps } } };
          } else {
            cardStates[i].editedAction = null;
          }
          _updateBatchSummary();
        }

        stepsContainer.addEventListener('input', _markPlanEdited);

        if (isBatch) {
          const chk = card.querySelector(`#ap-chk-${i}`);
          if (chk) chk.addEventListener('change', () => { cardStates[i].checked = chk.checked; _updateBatchSummary(); });
        } else {
          // Single-action mode: inline buttons
          function handlePlanDecision(type) {
            if (_planDecided) return;
            _planDecided = true;
            const actionsDiv = card.querySelector('.approval-actions');
            if (actionsDiv?.parentNode) actionsDiv.remove();
            card.querySelector('.plan-add-step')?.remove();
            stepsContainer.querySelectorAll('.plan-step-del').forEach(b => b.remove());
            stepsContainer.querySelectorAll('.plan-step-input').forEach(inp => { inp.readOnly = true; inp.classList.add('readonly'); });

            card.classList.add('decided', `decided-${type}`);
            const badge = document.createElement('div');
            badge.className = `approval-badge approval-badge-${type}`;
            badge.textContent = type === 'reject' ? '❌ 已拒绝' : '✅ 计划已批准';
            card.appendChild(badge);

            const finalSteps = _collectSteps();
            const stepsChanged = finalSteps.length !== steps.length || finalSteps.some((s, idx) => s !== steps[idx]);
            const decisionType = (type === 'approve' && stepsChanged) ? 'edit' : type;

            const decisions = actions.map((_, idx) => {
              if (idx !== i) return { type: 'approve' };
              if (decisionType === 'edit') {
                return { type: 'edit', edited_action: { name: 'propose_plan', args: { steps: finalSteps } } };
              }
              return { type: decisionType };
            });

            _doResume(decisions);
          }

          const approveBtn = card.querySelector('.btn-approve');
          const rejectBtn = card.querySelector('.btn-reject');
          if (approveBtn) approveBtn.addEventListener('click', () => handlePlanDecision('approve'));
          if (rejectBtn) rejectBtn.addEventListener('click', () => handlePlanDecision('reject'));
        }

        scrollToBottom();
        continue;
      }

      // ============ File approval (write_file / edit_file) ============
      const card = document.createElement('div');
      card.className = 'approval-card';
      card.dataset.actionIndex = i;
      allCards.push(card);

      const isWrite = action.name === 'write_file';
      const filePath = action.args?.path || '';
      const contentPreview = isWrite ? (action.args?.content || '') : (action.args?.new_string || '');
      const oldContent = isWrite ? '' : (action.args?.old_string || '');

      const checkboxHtml = isBatch
        ? `<div class="approval-card-select"><input type="checkbox" id="ap-chk-${i}" checked /><label for="ap-chk-${i}">批准</label></div>`
        : '';

      card.innerHTML = `
        <div class="approval-header">
          ${checkboxHtml}
          <span class="approval-icon">${isWrite ? '📝' : '✏️'}</span>
          <span class="approval-label">${isWrite ? '新建文件' : '修改文件'}</span>
          <span class="approval-path">${escapeHtml(filePath)}</span>
          <button class="btn btn-xs btn-view-detail" title="在文件面板中查看完整上下文">📂 查看详情</button>
        </div>
        <div class="approval-section">
          <div class="approval-section-label">变更预览
            <button class="approval-expand-btn" title="展开/收起">▼</button>
          </div>
          <div class="diff-container collapsed">
            <div class="diff-loading">加载文件上下文…</div>
          </div>
          <textarea class="approval-edit-area" style="display:none" spellcheck="false">${escapeHtml(contentPreview)}</textarea>
        </div>
        ${!isBatch ? `<div class="approval-actions">
          ${allowed.includes('approve') ? '<button class="btn btn-sm btn-approve">✅ 批准</button>' : ''}
          ${allowed.includes('edit') ? '<button class="btn btn-sm btn-edit">✏️ 编辑</button>' : ''}
          ${allowed.includes('reject') ? '<button class="btn btn-sm btn-reject">❌ 拒绝</button>' : ''}
        </div>` : `<div class="approval-actions">
          ${allowed.includes('edit') ? '<button class="btn btn-sm btn-edit">✏️ 编辑</button>' : ''}
        </div>`}
      `;
      bodyEl.appendChild(card);

      const diffContainer = card.querySelector('.diff-container');
      const editArea = card.querySelector('.approval-edit-area');
      const expandBtn = card.querySelector('.approval-expand-btn');

      let _cardDecided = false;

      card.querySelector('.btn-view-detail').addEventListener('click', () => {
        if (typeof Files !== 'undefined') {
          Files.showFileReview({
            filePath,
            isWrite,
            oldContent,
            newContent: contentPreview,
            onDecision: (type) => handleDecision(type),
          });
        }
      });

      if (isWrite) {
        const lines = contentPreview.split('\n');
        const entries = lines.map(t => ({ type: 'add', text: t }));
        diffContainer.innerHTML = _diffTableHtml(entries, 1, 1);
      } else {
        (async () => {
          try {
            const { content } = await API.readFile(filePath);
            const fileLines = content.split('\n');
            const oldLines = oldContent.split('\n');
            const newLines = contentPreview.split('\n');

            const matchIdx = content.indexOf(oldContent);
            let oStart = 1;
            if (matchIdx >= 0) {
              oStart = content.substring(0, matchIdx).split('\n').length;
            }

            const ctxBefore = [];
            for (let li = Math.max(0, oStart - 1 - DIFF_CTX); li < oStart - 1; li++) {
              ctxBefore.push({ type: 'eq', text: fileLines[li] });
            }

            const changeDiff = _computeLineDiff(oldLines, newLines);

            const endIdx = oStart - 1 + oldLines.length;
            const ctxAfter = [];
            for (let li = endIdx; li < Math.min(fileLines.length, endIdx + DIFF_CTX); li++) {
              ctxAfter.push({ type: 'eq', text: fileLines[li] });
            }

            const fullDiff = [...ctxBefore, ...changeDiff, ...ctxAfter];
            const firstLine = Math.max(1, oStart - ctxBefore.length);
            diffContainer.innerHTML = _diffTableHtml(fullDiff, firstLine, firstLine);
          } catch {
            const diff = _computeLineDiff(oldContent.split('\n'), contentPreview.split('\n'));
            diffContainer.innerHTML = _diffTableHtml(diff, 1, 1);
          }
        })();
      }

      expandBtn.addEventListener('click', () => {
        diffContainer.classList.toggle('collapsed');
        expandBtn.textContent = diffContainer.classList.contains('collapsed') ? '▼' : '▲';
      });

      // Batch mode: checkbox + edit tracking
      if (isBatch) {
        const chk = card.querySelector(`#ap-chk-${i}`);
        if (chk) chk.addEventListener('change', () => { cardStates[i].checked = chk.checked; _updateBatchSummary(); });

        const editBtn = card.querySelector('.btn-edit');
        if (editBtn) editBtn.addEventListener('click', () => {
          if (editArea.style.display === 'none') {
            editArea.style.display = 'block';
            diffContainer.style.display = 'none';
            editBtn.textContent = '💾 确认编辑';
            expandBtn.style.display = 'none';
          } else {
            editArea.style.display = 'none';
            diffContainer.style.display = '';
            editBtn.textContent = '✏️ 编辑';
            expandBtn.style.display = '';

            const edited = { ...action.args };
            const editedContent = editArea.value;
            if (isWrite) edited.content = editedContent;
            else edited.new_string = editedContent;
            cardStates[i].edited = true;
            cardStates[i].editedAction = { type: 'edit', edited_action: { name: action.name, args: edited } };
            _updateBatchSummary();
          }
        });
      } else {
        // Single-action mode: inline approve/edit/reject
        function handleDecision(type) {
          if (_cardDecided) return;
          _cardDecided = true;

          const actionsDiv = card.querySelector('.approval-actions');
          if (actionsDiv?.parentNode) actionsDiv.remove();
          card.classList.add('decided', `decided-${type}`);
          const badge = document.createElement('div');
          badge.className = `approval-badge approval-badge-${type}`;
          badge.textContent = type === 'approve' ? '✅ 已批准' : type === 'reject' ? '❌ 已拒绝' : '✏️ 已编辑并批准';
          card.appendChild(badge);

          if (typeof Files !== 'undefined') Files.markReviewDecided();

          const decisions = actions.map((_, idx) => {
            if (idx !== i) return { type: 'approve' };
            if (type === 'edit') {
              const edited = { ...action.args };
              const editedContent = editArea.value;
              if (isWrite) edited.content = editedContent;
              else edited.new_string = editedContent;
              return { type: 'edit', edited_action: { name: action.name, args: edited } };
            }
            return { type };
          });

          _doResume(decisions);
        }

        const approveBtn = card.querySelector('.btn-approve');
        const editBtn = card.querySelector('.btn-edit');
        const rejectBtn = card.querySelector('.btn-reject');

        if (approveBtn) approveBtn.addEventListener('click', () => handleDecision('approve'));
        if (rejectBtn) rejectBtn.addEventListener('click', () => handleDecision('reject'));
        if (editBtn) editBtn.addEventListener('click', () => {
          if (editArea.style.display === 'none') {
            editArea.style.display = 'block';
            diffContainer.style.display = 'none';
            editBtn.textContent = '💾 确认编辑';
            expandBtn.style.display = 'none';
          } else {
            handleDecision('edit');
          }
        });
      }
    }

    // ---- Batch toolbar ----
    if (isBatch) {
      batchToolbar = document.createElement('div');
      batchToolbar.className = 'batch-approval-toolbar';
      batchToolbar.innerHTML = `
        <span class="batch-summary"></span>
        <button class="btn btn-sm btn-approve" id="batch-approve">✅ 批准已选</button>
        <button class="btn btn-sm btn-reject" id="batch-reject-all">❌ 全部拒绝</button>
      `;
      bodyEl.appendChild(batchToolbar);

      batchToolbar.querySelector('#batch-approve').addEventListener('click', _batchSubmit);
      batchToolbar.querySelector('#batch-reject-all').addEventListener('click', _batchRejectAll);
      _updateBatchSummary();
    }
  }

  // 媒体文件扩展名分类
  const MEDIA_EXTS = {
    image: ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.ico'],
    audio: ['.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac', '.wma'],
    video: ['.mp4', '.webm', '.ogv', '.mov', '.avi', '.mkv'],
    pdf:   ['.pdf'],
    html:  ['.html', '.htm'],
  };

  function getMediaType(filePath) {
    const ext = filePath.slice(filePath.lastIndexOf('.')).toLowerCase();
    for (const [type, exts] of Object.entries(MEDIA_EXTS)) {
      if (exts.includes(ext)) return type;
    }
    return null;
  }

  /**
   * 将文件路径转换为内嵌媒体 HTML
   */
  function filePathToMedia(filePath) {
    const type = getMediaType(filePath);
    if (!type) return null;

    const url = API.mediaUrl(filePath);
    const fileName = filePath.split('/').pop();

    switch (type) {
      case 'image':
        return `<div class="media-embed media-image">
          <img src="${url}" alt="${escapeHtml(fileName)}" loading="lazy"
               onclick="window.open(this.src, '_blank')" title="点击查看大图" />
          <div class="media-caption">${escapeHtml(fileName)}</div>
        </div>`;

      case 'audio':
        return `<div class="media-embed media-audio">
          <div class="media-audio-header">🎵 ${escapeHtml(fileName)}</div>
          <audio controls preload="metadata" src="${url}">
            浏览器不支持音频播放
          </audio>
        </div>`;

      case 'video':
        return `<div class="media-embed media-video">
          <video controls preload="metadata" src="${url}">
            浏览器不支持视频播放
          </video>
          <div class="media-caption">${escapeHtml(fileName)}</div>
        </div>`;

      case 'pdf':
        return `<div class="media-embed media-pdf">
          <div class="media-pdf-header">
            📄 ${escapeHtml(fileName)}
            <a href="${url}" target="_blank" class="media-pdf-open">在新窗口打开</a>
          </div>
          <iframe src="${url}" class="pdf-viewer"></iframe>
        </div>`;

      case 'html': {
        const htmlId = 'html-embed-' + Math.random().toString(36).slice(2, 10);
        return `<div class="media-embed media-html" id="${htmlId}">
          <div class="media-html-header">
            <span class="media-html-title">📊 ${escapeHtml(fileName)}</span>
            <div class="media-html-actions">
              <button class="media-html-btn" onclick="document.getElementById('${htmlId}').classList.toggle('media-html-expanded')" title="展开/收起">⛶</button>
              <a href="${url}" target="_blank" class="media-html-btn" title="在新窗口打开">↗</a>
            </div>
          </div>
          <iframe src="${url}"
                  class="html-viewer"
                  sandbox="allow-scripts allow-same-origin allow-popups"
                  loading="lazy"
                  frameborder="0">
          </iframe>
        </div>`;
      }

      default:
        return null;
    }
  }

  /** 所有支持的媒体扩展名（用于正则） */
  const ALL_MEDIA_EXTS = 'jpg|jpeg|png|gif|webp|svg|bmp|ico|mp3|wav|ogg|m4a|flac|aac|wma|mp4|webm|ogv|mov|avi|mkv|pdf|html|htm';

  /**
   * 媒体文件渲染 —— 基于特殊标签 <<FILE:path>>
   *
   * Agent 在回复中使用 <<FILE:/path/to/file.ext>> 来展示媒体文件。
   * 这个标签格式简洁明确，不会和 markdown 语法冲突。
   *
   * 渲染流程：
   *  1) 预处理：检测 <<FILE:path>> 标签，替换为 HTML 占位标记
   *  2) marked.parse() 渲染 markdown（占位标记被保留）
   *  3) 后处理：将占位标记替换为媒体嵌入 HTML
   */

  const FILE_TAG_REGEX = /<?<FILE:(\/[^>]+?)>>?/gi;

  /**
   * 预处理：在 markdown 渲染前，将 <<FILE:path>> 标签替换为 HTML 占位标记
   */
  function preProcessMediaTags(text) {
    return text.replace(FILE_TAG_REGEX, (match, filePath) => {
      const trimmed = filePath.trim();
      const media = filePathToMedia(trimmed);
      if (media) {
        return `\n\n<div data-media-path="${escapeHtml(trimmed)}">${media}</div>\n\n`;
      }
      return match;
    });
  }

  /**
   * 后处理 HTML：修正 marked 生成的 <img> 标签（来自 ![...](/path) 语法）
   * 将本地文件路径的 src 替换为带认证的 media URL
   */
  function postProcessMedia(html) {
    const extPattern = `\\.(?:${ALL_MEDIA_EXTS})`;

    // 修正 marked 生成的 <img> 标签
    const imgRegex = new RegExp(
      `<img\\s+src="(\\/[^"]+${extPattern})"([^>]*)>`, 'gi'
    );
    html = html.replace(imgRegex, (match, path, rest) => {
      if (path.includes('/api/files/media')) return match;
      const decodedPath = decodeURIComponent(path);
      const url = API.mediaUrl(decodedPath);
      const fileName = decodedPath.split('/').pop();
      return `<div class="media-embed media-image">
        <img src="${url}" ${rest} loading="lazy"
             onclick="window.open(this.src, '_blank')" title="点击查看大图" />
        <div class="media-caption">${escapeHtml(fileName)}</div>
      </div>`;
    });

    return html;
  }

  /** Markdown 渲染（含媒体文件检测） */
  function renderMarkdown(text) {
    // 第一步：预处理 — 将 <<FILE:path>> 标签转为媒体嵌入 HTML
    const preprocessed = preProcessMediaTags(text);

    // 第二步：Markdown 渲染
    let html;
    if (typeof marked !== 'undefined') {
      try {
        html = marked.parse(preprocessed);
      } catch {
        html = escapeHtml(preprocessed).replace(/\n/g, '<br>');
      }
    } else {
      html = escapeHtml(preprocessed).replace(/\n/g, '<br>');
    }

    // 第三步：后处理 — 修正 markdown 图片的 src（![](path) 也作为兜底支持）
    return postProcessMedia(html);
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  /**
   * 智能滚动系统：
   *  - 流式输出时，如果用户没有手动上滚，自动吸底
   *  - 如果用户手动上滚查看历史内容，暂停自动滚动
   *  - 用户滚回底部附近时，恢复自动吸底
   */
  let _userScrolledUp = false;
  const SCROLL_THRESHOLD = 60; // 距底部多少 px 以内视为"在底部"

  // 监听滚动事件，检测用户是否手动上滚
  (function initScrollWatcher() {
    const container = document.getElementById('chat-messages');
    const scrollBtn = document.getElementById('btn-scroll-bottom');
    if (!container) return;

    function updateScrollBtn() {
      if (!scrollBtn) return;
      if (_userScrolledUp && isStreaming) {
        scrollBtn.classList.add('visible');
      } else {
        scrollBtn.classList.remove('visible');
      }
    }

    container.addEventListener('scroll', () => {
      const distFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
      if (distFromBottom <= SCROLL_THRESHOLD) {
        _userScrolledUp = false;
      } else if (isStreaming) {
        _userScrolledUp = true;
      }
      updateScrollBtn();
    });

    // 点击按钮回到底部
    if (scrollBtn) {
      scrollBtn.addEventListener('click', () => {
        _userScrolledUp = false;
        scrollToBottom(true);
        updateScrollBtn();
      });
    }
  })();

  /**
   * scrollToBottom：如果用户没有手动上滚，滚到底部
   * @param {boolean} force - 是否强制滚到底部（忽略用户滚动状态）
   */
  function scrollToBottom(force = false) {
    if (_userScrolledUp && !force) return;

    const container = document.getElementById('chat-messages');
    requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
    });
  }

  function getCurrentConvId() {
    return currentConvId;
  }

  return { init, loadConversations, createNewChat, selectConversation, getCurrentConvId, sendMessageFrom };
})();
