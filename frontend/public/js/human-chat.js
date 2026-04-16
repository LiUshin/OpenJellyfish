/**
 * HumanChat Mode — iMessage-style phone simulator
 *
 * Only content sent via the agent's `send_message` tool
 * is rendered inside this phone panel. The original chat
 * area continues to show the full streaming output.
 */
const HumanChat = (function () {
  'use strict';

  let _active = false;
  let _convId = null;
  const _msgQueue = [];
  let _queueProcessing = false;
  const MSG_DELAY = 600;

  const AUDIO_EXTS = /\.(mp3|wav|ogg|m4a|flac|aac|wma)$/i;
  const IMAGE_EXTS = /\.(jpg|jpeg|png|gif|webp|svg|bmp)$/i;
  const VIDEO_EXTS = /\.(mp4|webm|ogv|mov)$/i;

  function getPanel()    { return document.getElementById('hc-panel'); }
  function getMessages() { return document.getElementById('hc-messages'); }

  // ==================== Open / Close ====================

  function open(convId) {
    _convId = convId;
    _active = true;

    const panel = getPanel();
    panel.style.display = '';
    document.body.classList.add('humanchat-active');
    document.getElementById('btn-humanchat').classList.add('active');

    clearMessages();
    if (convId) {
      restoreHistory(convId);
    }
  }

  function close() {
    _active = false;
    _convId = null;

    const panel = getPanel();
    panel.style.display = 'none';
    document.body.classList.remove('humanchat-active');
    document.getElementById('btn-humanchat').classList.remove('active');
  }

  function toggle(convId) {
    if (_active) {
      close();
    } else {
      open(convId);
    }
  }

  function isActive() { return _active; }

  // ==================== Message Rendering ====================

  function clearMessages() {
    const container = getMessages();
    container.innerHTML = '<div class="hc-time-label">今天</div>';
  }

  function scrollToBottom() {
    const container = getMessages();
    container.scrollTop = container.scrollHeight;
  }

  function addUserMessage(text) {
    const container = getMessages();
    hideTyping();
    const bubble = document.createElement('div');
    bubble.className = 'hc-msg sent';
    bubble.textContent = text;
    container.appendChild(bubble);
    scrollToBottom();
  }

  /**
   * Queue an agent message for delayed rendering (simulates typing).
   * @param {object} data  — parsed JSON: { text, media? }
   */
  function addAgentMessage(data) {
    _msgQueue.push(data);
    _processQueue();
  }

  function _processQueue() {
    if (_queueProcessing || _msgQueue.length === 0) return;
    _queueProcessing = true;

    const data = _msgQueue.shift();
    _renderAgentBubble(data);

    if (_msgQueue.length > 0) {
      showTyping();
      setTimeout(() => {
        _queueProcessing = false;
        _processQueue();
      }, MSG_DELAY);
    } else {
      _queueProcessing = false;
    }
  }

  function _renderAgentBubble(data) {
    const container = getMessages();
    hideTyping();

    const bubble = document.createElement('div');
    bubble.className = 'hc-msg received';

    if (data.media) {
      const mediaEl = renderMedia(data.media);
      if (mediaEl) bubble.appendChild(mediaEl);
    }

    if (data.text) {
      const textEl = document.createElement('span');
      textEl.textContent = data.text;
      bubble.appendChild(textEl);
    }

    container.appendChild(bubble);
    scrollToBottom();
  }

  // ==================== Media Renderers ====================

  function renderMedia(filePath) {
    const url = API.mediaUrl(filePath);
    const fileName = filePath.split('/').pop();

    if (AUDIO_EXTS.test(filePath)) {
      return createVoiceBar(url, fileName);
    }
    if (IMAGE_EXTS.test(filePath)) {
      return createImagePreview(url, fileName);
    }
    if (VIDEO_EXTS.test(filePath)) {
      return createVideoPreview(url);
    }
    return createFileCard(url, fileName);
  }

  function createImagePreview(url, alt) {
    const img = document.createElement('img');
    img.src = url;
    img.alt = alt;
    img.className = 'hc-msg-image';
    img.loading = 'lazy';
    img.addEventListener('click', () => window.open(url, '_blank'));
    return img;
  }

  function createVideoPreview(url) {
    const video = document.createElement('video');
    video.src = url;
    video.controls = true;
    video.preload = 'metadata';
    video.style.maxWidth = '200px';
    video.style.borderRadius = '14px';
    return video;
  }

  function createFileCard(url, fileName) {
    const a = document.createElement('a');
    a.className = 'hc-msg-file';
    a.href = url;
    a.target = '_blank';
    a.textContent = '📎 ' + fileName;
    return a;
  }

  /**
   * iMessage-style voice bar with waveform visualization.
   */
  function createVoiceBar(url, fileName) {
    const wrapper = document.createElement('div');
    wrapper.className = 'hc-voice-bar';

    const playBtn = document.createElement('button');
    playBtn.className = 'hc-voice-play-btn';
    playBtn.textContent = '▶';

    const waveform = document.createElement('div');
    waveform.className = 'hc-voice-waveform';
    const barCount = 20;
    const bars = [];
    for (let i = 0; i < barCount; i++) {
      const bar = document.createElement('span');
      const h = 4 + Math.random() * 14;
      bar.style.height = h + 'px';
      waveform.appendChild(bar);
      bars.push(bar);
    }

    const durationEl = document.createElement('span');
    durationEl.className = 'hc-voice-duration';
    durationEl.textContent = '···';

    const audio = new Audio(url);
    audio.preload = 'metadata';
    let playing = false;

    audio.addEventListener('loadedmetadata', () => {
      const dur = Math.ceil(audio.duration);
      durationEl.textContent = dur + '"';
    });

    audio.addEventListener('timeupdate', () => {
      if (!audio.duration) return;
      const progress = audio.currentTime / audio.duration;
      const activeCount = Math.floor(progress * barCount);
      bars.forEach((b, i) => {
        b.classList.toggle('active', i < activeCount);
      });
    });

    audio.addEventListener('ended', () => {
      playing = false;
      playBtn.textContent = '▶';
      bars.forEach(b => b.classList.remove('active'));
    });

    playBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (playing) {
        audio.pause();
        playBtn.textContent = '▶';
      } else {
        audio.play();
        playBtn.textContent = '⏸';
      }
      playing = !playing;
    });

    wrapper.appendChild(playBtn);
    wrapper.appendChild(waveform);
    wrapper.appendChild(durationEl);
    return wrapper;
  }

  // ==================== Typing Indicator ====================

  function showTyping() {
    const container = getMessages();
    if (container.querySelector('.hc-typing')) return;

    const typing = document.createElement('div');
    typing.className = 'hc-typing';
    typing.innerHTML =
      '<div class="hc-typing-dot"></div>' +
      '<div class="hc-typing-dot"></div>' +
      '<div class="hc-typing-dot"></div>';
    container.appendChild(typing);
    scrollToBottom();
  }

  function hideTyping() {
    const container = getMessages();
    const typing = container.querySelector('.hc-typing');
    if (typing) typing.remove();
  }

  // ==================== History Restoration ====================

  /**
   * Scan conversation history for send_message tool results
   * and rebuild the phone chat from them.
   */
  async function restoreHistory(convId) {
    try {
      const conv = await API.getConversation(convId);
      if (!conv || !conv.messages) return;

      for (const msg of conv.messages) {
        if (msg.role === 'user') {
          addUserMessage(msg.content);
        } else if (msg.role === 'assistant' && msg.content) {
          const toolResultRegex = /send_message.*?"content"\s*:\s*"(.*?)"/gs;
          // We rely on the streaming interception for live messages.
          // For history, we scan tool_calls embedded in the stored content.
          // This is best-effort; the primary path is live interception.
        }
      }

      // A more reliable approach: look for the tool_result pattern in messages.
      // The stored messages are simple {role, content} pairs.
      // send_message results are captured during streaming, not in stored history.
      // We'll parse the assistant content for any JSON blocks that look like send_message output.
      for (const msg of conv.messages) {
        if (msg.role !== 'assistant') continue;
        const content = msg.content || '';
        // Try to find send_message JSON patterns: {"text": "...", "media": "..."}
        const jsonPattern = /\{"text"\s*:\s*"[^"]*"(?:\s*,\s*"media"\s*:\s*"[^"]*")?\}/g;
        let match;
        while ((match = jsonPattern.exec(content)) !== null) {
          try {
            const data = JSON.parse(match[0]);
            if (data.text) {
              const bubble = document.createElement('div');
              bubble.className = 'hc-msg received';
              bubble.textContent = data.text;
              getMessages().appendChild(bubble);
            }
          } catch { /* skip */ }
        }
      }

      scrollToBottom();
    } catch (err) {
      console.warn('[HumanChat] Failed to restore history:', err);
    }
  }

  // ==================== Input Handling ====================

  function handleSend() {
    const input = document.getElementById('hc-input');
    const text = input.value.trim();
    if (!text) return;

    input.value = '';

    if (typeof Chat !== 'undefined' && Chat.sendMessageFrom) {
      Chat.sendMessageFrom(text);
    }
  }

  // ==================== Init ====================

  function init() {
    const sendBtn = document.getElementById('hc-send');
    const input   = document.getElementById('hc-input');
    const closeBtn = document.getElementById('hc-close');

    if (sendBtn) {
      sendBtn.addEventListener('click', handleSend);
    }
    if (input) {
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          handleSend();
        }
      });
    }
    if (closeBtn) {
      closeBtn.addEventListener('click', close);
    }
  }

  // ==================== Public API ====================

  return {
    init,
    open,
    close,
    toggle,
    isActive,
    addUserMessage,
    addAgentMessage,
    showTyping,
    hideTyping,
  };
})();
