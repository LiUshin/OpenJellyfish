/**
 * Voice Agent Module — S2S Realtime with chat panel
 *
 * Left side: mic + waveform + status
 * Right side: scrollable chat history (user bubbles, assistant bubbles, tool cards)
 */

const VoiceAgent = (() => {
  let _active = false;
  let _state = 'idle';
  let _ws = null;
  let _s2sAudioCtx = null;
  let _s2sStream = null;
  let _s2sProcessor = null;
  let _s2sPlaying = false;
  let _s2sAudioQueue = [];
  let _s2sPlaybackEpoch = 0;
  let _s2sPlaybackCtx = null;

  // Current streaming assistant bubble (appended to as deltas arrive)
  let _currentAssistantBubble = null;

  function getOverlay() { return document.getElementById('voice-agent-overlay'); }
  function getChatContainer() { return getOverlay()?.querySelector('.va-chat-messages'); }

  // ─── Open / Close ─────────────────────────────────────────────────

  function open() {
    const overlay = getOverlay();
    if (!overlay) return;
    overlay.classList.add('open');
    _active = true;
    _currentAssistantBubble = null;
    clearChat();
    setState('idle');
    connectS2S();
  }

  function close() {
    disconnectS2S();
    const overlay = getOverlay();
    if (overlay) overlay.classList.remove('open');
    _active = false;
    setState('idle');
  }

  // ─── Chat panel helpers ───────────────────────────────────────────

  function clearChat() {
    const c = getChatContainer();
    if (c) c.innerHTML = '<div class="va-msg-empty">Start talking — conversation will appear here</div>';
  }

  function ensureEmptyGone() {
    const c = getChatContainer();
    const empty = c?.querySelector('.va-msg-empty');
    if (empty) empty.remove();
  }

  function addBubble(role, text) {
    ensureEmptyGone();
    const c = getChatContainer();
    if (!c) return null;
    const div = document.createElement('div');
    div.className = `va-msg ${role}`;
    div.textContent = text || '';
    c.appendChild(div);
    c.scrollTop = c.scrollHeight;
    return div;
  }

  const FILE_TAG_RE = /<<FILE:(\/[^>]+?)>>/gi;
  const IMG_EXTS = /\.(png|jpe?g|gif|webp|svg|bmp)$/i;
  const AUDIO_EXTS = /\.(mp3|wav|ogg|m4a|flac|aac)$/i;
  const VIDEO_EXTS = /\.(mp4|webm|mov|mkv|avi)$/i;

  function renderFileTag(path) {
    const url = API.mediaUrl(path);
    if (IMG_EXTS.test(path)) {
      return `<img src="${url}" alt="${path}" style="max-width:100%;border-radius:8px;margin-top:6px;cursor:pointer" onclick="window.open('${url}','_blank')">`;
    }
    if (AUDIO_EXTS.test(path)) {
      return `<audio controls src="${url}" style="width:100%;margin-top:6px"></audio>`;
    }
    if (VIDEO_EXTS.test(path)) {
      return `<video controls src="${url}" style="max-width:100%;border-radius:8px;margin-top:6px"></video>`;
    }
    return `<a href="${url}" target="_blank" style="color:var(--accent)">${path}</a>`;
  }

  function renderWithFileTags(text) {
    return text.replace(FILE_TAG_RE, (_, path) => renderFileTag(path));
  }

  function addToolCard(toolName, resultPreview) {
    ensureEmptyGone();
    const c = getChatContainer();
    if (!c) return;
    const div = document.createElement('div');
    div.className = 'va-msg tool';
    const label = document.createElement('div');
    label.className = 'va-tool-label';
    label.textContent = `🔧 ${toolName}`;
    div.appendChild(label);
    if (resultPreview) {
      const body = document.createElement('div');
      if (FILE_TAG_RE.test(resultPreview)) {
        body.innerHTML = renderWithFileTags(resultPreview);
      } else {
        body.textContent = resultPreview;
      }
      div.appendChild(body);
    }
    c.appendChild(div);
    c.scrollTop = c.scrollHeight;
  }

  // ─── UI state ─────────────────────────────────────────────────────

  function setState(newState) {
    _state = newState;
    const overlay = getOverlay();
    if (!overlay) return;

    overlay.dataset.state = newState;

    const statusText = overlay.querySelector('.va-status-text');
    const waveform = overlay.querySelector('.va-waveform');

    const labels = {
      idle: 'Tap mic or press Space to start',
      processing: 'Thinking...',
      playing: 'Speaking...',
      listening: 'Listening...',
      connecting: 'Connecting...',
      tool_running: 'Using tool...',
    };
    if (statusText) statusText.textContent = labels[newState] || '';
    if (waveform) waveform.classList.toggle('active', newState === 'listening');
  }

  // ─── S2S WebSocket ────────────────────────────────────────────────

  function connectS2S() {
    if (_ws && _ws.readyState <= WebSocket.OPEN) return;
    _ws = null;
    setState('connecting');

    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const pathPrefix = location.pathname.startsWith('/jellyfishbot') ? '/jellyfishbot' : '';
    const url = `${proto}://${location.host}${pathPrefix}/api/voice/realtime?token=${encodeURIComponent(API.getToken())}`;

    _ws = new WebSocket(url);

    _ws.onopen = () => {
      _ws.send(JSON.stringify({
        type: 'session.update',
        session: {
          modalities: ['text', 'audio'],
          voice: getOverlay()?.querySelector('.va-voice-select')?.value || 'alloy',
          input_audio_format: 'pcm16',
          output_audio_format: 'pcm16',
          turn_detection: {
            type: 'server_vad',
            threshold: 0.65,
            prefix_padding_ms: 500,
            silence_duration_ms: 1500,
          },
          input_audio_transcription: { model: 'whisper-1' },
          max_response_output_tokens: 4096,
          instructions: 'You are a smart voice assistant with access to file operations and script execution tools. Speak naturally and conversationally, like chatting with a friend. Keep answers short and to the point. When you use a tool, casually tell the user what you found or did.',
        },
      }));
      startAudioCapture();
      setState('listening');
    };

    _ws.onmessage = (event) => {
      try {
        handleMessage(JSON.parse(event.data));
      } catch {}
    };

    _ws.onerror = () => { App.toast('Realtime connection error', 'error'); };

    _ws.onclose = () => {
      _ws = null;
      if (_active) setState('idle');
    };
  }

  function disconnectS2S() {
    stopAudioCapture();
    if (_ws) { try { _ws.close(); } catch {} _ws = null; }
    _s2sAudioQueue = [];
    _s2sPlaybackEpoch++;
    _s2sPlaying = false;
    if (_s2sPlaybackCtx && _s2sPlaybackCtx.state !== 'closed') {
      try { _s2sPlaybackCtx.close(); } catch {} _s2sPlaybackCtx = null;
    }
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case 'session.created':
      case 'session.updated':
        break;

      case 'response.audio.delta':
        if (msg.delta) {
          _s2sAudioQueue.push(msg.delta);
          if (!_s2sPlaying) playAudioQueue();
        }
        break;

      case 'response.audio_transcript.delta':
        if (msg.delta) {
          if (!_currentAssistantBubble) {
            _currentAssistantBubble = addBubble('assistant', '');
          }
          _currentAssistantBubble.textContent += msg.delta;
          const c = getChatContainer();
          if (c) c.scrollTop = c.scrollHeight;
        }
        break;

      case 'input_audio_buffer.speech_started':
        _s2sAudioQueue = [];
        _s2sPlaybackEpoch++;
        _s2sPlaying = false;
        setState('listening');
        _currentAssistantBubble = null;
        break;

      case 'input_audio_buffer.speech_stopped':
        setState('processing');
        break;

      case 'conversation.item.input_audio_transcription.completed':
        if (msg.transcript) {
          addBubble('user', msg.transcript);
        }
        break;

      case 'response.audio.done':
        _currentAssistantBubble = null;
        break;

      case 'response.done':
        _currentAssistantBubble = null;
        if (!_s2sPlaying && _s2sAudioQueue.length === 0) {
          setState(_s2sStream ? 'listening' : 'idle');
        }
        break;

      case 's2s.tool_call': {
        const statusEl = getOverlay()?.querySelector('.va-status-text');
        if (msg.status === 'running') {
          setState('tool_running');
          const elapsed = msg.elapsed ? ` (${msg.elapsed}s)` : '';
          if (statusEl) statusEl.textContent = `Using tool: ${msg.tool_name}...${elapsed}`;
        } else if (msg.status === 'done') {
          addToolCard(msg.tool_name, msg.result_preview || '');
          setState('processing');
        }
        break;
      }

      case 'error':
        App.toast('Realtime: ' + (msg.error?.message || 'Unknown error'), 'error');
        setState('idle');
        break;
    }
  }

  // ─── Audio capture ────────────────────────────────────────────────

  async function startAudioCapture() {
    if (_s2sStream) return;
    try {
      _s2sStream = await navigator.mediaDevices.getUserMedia({ audio: { sampleRate: 24000, channelCount: 1 } });
      _s2sAudioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
      const source = _s2sAudioCtx.createMediaStreamSource(_s2sStream);
      _s2sProcessor = _s2sAudioCtx.createScriptProcessor(4096, 1, 1);

      _s2sProcessor.onaudioprocess = (e) => {
        if (!_ws || _ws.readyState !== WebSocket.OPEN) return;
        const pcm = e.inputBuffer.getChannelData(0);
        const int16 = new Int16Array(pcm.length);
        for (let i = 0; i < pcm.length; i++) {
          int16[i] = Math.max(-32768, Math.min(32767, Math.floor(pcm[i] * 32768)));
        }
        const bytes = new Uint8Array(int16.buffer);
        let binary = '';
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        _ws.send(JSON.stringify({ type: 'input_audio_buffer.append', audio: btoa(binary) }));
      };

      source.connect(_s2sProcessor);
      _s2sProcessor.connect(_s2sAudioCtx.destination);
    } catch (err) {
      App.toast('Mic error: ' + err.message, 'error');
    }
  }

  function stopAudioCapture() {
    if (_s2sProcessor) { try { _s2sProcessor.disconnect(); } catch {} _s2sProcessor = null; }
    if (_s2sAudioCtx) { try { _s2sAudioCtx.close(); } catch {} _s2sAudioCtx = null; }
    if (_s2sStream) { _s2sStream.getTracks().forEach(t => t.stop()); _s2sStream = null; }
  }

  // ─── Audio playback ───────────────────────────────────────────────

  function decodeB64PCM16(b64) {
    const bin = atob(b64);
    const len = bin.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
    return new Int16Array(bytes.buffer);
  }

  function drainQueueToFloat32() {
    let totalSamples = 0;
    const chunks = [];
    while (_s2sAudioQueue.length > 0) {
      const int16 = decodeB64PCM16(_s2sAudioQueue.shift());
      chunks.push(int16);
      totalSamples += int16.length;
    }
    const merged = new Float32Array(totalSamples);
    let offset = 0;
    for (const c of chunks) {
      for (let i = 0; i < c.length; i++) merged[offset++] = c[i] / 32768;
    }
    return merged;
  }

  async function playAudioQueue() {
    if (_s2sPlaying) return;
    _s2sPlaying = true;
    setState('playing');

    const epoch = ++_s2sPlaybackEpoch;

    if (!_s2sPlaybackCtx || _s2sPlaybackCtx.state === 'closed') {
      _s2sPlaybackCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
    }
    const ctx = _s2sPlaybackCtx;
    let nextStartTime = ctx.currentTime;

    while (true) {
      if (epoch !== _s2sPlaybackEpoch) break;

      if (_s2sAudioQueue.length > 0) {
        const float32 = drainQueueToFloat32();
        if (float32.length === 0) continue;

        const buffer = ctx.createBuffer(1, float32.length, 24000);
        buffer.getChannelData(0).set(float32);

        const src = ctx.createBufferSource();
        src.buffer = buffer;
        src.connect(ctx.destination);

        const startAt = Math.max(nextStartTime, ctx.currentTime);
        src.start(startAt);
        nextStartTime = startAt + buffer.duration;
      } else {
        const remaining = nextStartTime - ctx.currentTime;
        if (remaining > 0.05) {
          await new Promise(r => setTimeout(r, 40));
        } else {
          await new Promise(r => setTimeout(r, 20));
        }
        if (_s2sAudioQueue.length === 0) break;
      }
    }

    if (epoch === _s2sPlaybackEpoch) {
      const remaining = nextStartTime - ctx.currentTime;
      if (remaining > 0) {
        await new Promise(r => setTimeout(r, remaining * 1000 + 20));
      }
      _s2sPlaying = false;
      setState(_s2sStream ? 'listening' : 'idle');
    }
  }

  // ─── Mic toggle (continuous conversation) ───────────────────────

  function toggleMic(e) {
    if (e) e.preventDefault();

    if (_state === 'idle' || _state === 'connecting') {
      if (!_ws || _ws.readyState !== WebSocket.OPEN) {
        connectS2S();
        return;
      }
      startAudioCapture();
      setState('listening');
    } else {
      stopAudioCapture();
      _s2sAudioQueue = [];
      _s2sPlaybackEpoch++;
      _s2sPlaying = false;
      setState('idle');
    }
  }

  // ─── Init ─────────────────────────────────────────────────────────

  function init() {
    const overlay = getOverlay();
    if (!overlay) return;

    overlay.querySelector('.va-close-btn')?.addEventListener('click', close);

    const micBtn = overlay.querySelector('.va-mic-btn');
    if (micBtn) {
      micBtn.addEventListener('click', toggleMic);
    }

    document.addEventListener('keydown', (e) => {
      if (!_active || e.repeat) return;
      if (e.key === 'Escape') { close(); return; }
      if (e.code === 'Space' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) {
        e.preventDefault();
        toggleMic(e);
      }
    });
  }

  return { init, open, close };
})();
