/**
 * Admin WeChat — sidebar panel for connecting admin's
 * main agent via WeChat QR scan.
 */
const AdminWeChat = (() => {
  let _pollTimer = null;
  let _currentQrUrl = null;

  const esc = (s) => {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  };

  function overlay()   { return document.getElementById('admin-wechat-overlay'); }
  function bodyEl()    { return document.getElementById('awc-body'); }

  function show() {
    const el = overlay();
    if (!el) return;
    el.style.display = '';
    _checkSession().catch(() => _showDisconnected());
  }

  function hide() {
    overlay().style.display = 'none';
    _stopPolling();
  }

  // ── session check ──

  async function _checkSession() {
    try {
      const data = await API.request('GET', '/admin/wechat/session');
      if (data.connected) {
        _showConnected(data);
      } else {
        _showDisconnected();
      }
    } catch {
      _showDisconnected();
    }
  }

  function _showDisconnected() {
    document.getElementById('awc-disconnected').style.display = '';
    document.getElementById('awc-qr-area').style.display = 'none';
    document.getElementById('awc-connected').style.display = 'none';
    const btn = document.getElementById('btn-awc-gen-qr');
    if (btn) { btn.disabled = false; btn.textContent = '生成微信二维码'; }
  }

  function _showQR() {
    document.getElementById('awc-disconnected').style.display = 'none';
    document.getElementById('awc-qr-area').style.display = '';
    document.getElementById('awc-connected').style.display = 'none';
  }

  function _showConnected(data) {
    document.getElementById('awc-disconnected').style.display = 'none';
    document.getElementById('awc-qr-area').style.display = 'none';
    document.getElementById('awc-connected').style.display = '';
    const at = document.getElementById('awc-connected-at');
    at.textContent = (data.connected_at || '').slice(0, 16);
    _loadMessages();
  }

  // ── QR generation ──

  async function _generateQR() {
    const btn = document.getElementById('btn-awc-gen-qr');
    btn.disabled = true;
    btn.textContent = '生成中...';
    try {
      const data = await API.request('POST', '/admin/wechat/qrcode');
      const img = document.getElementById('awc-qr-img');
      img.src = 'data:image/png;base64,' + data.qr_image_b64;
      _currentQrUrl = data.qr_id;
      document.getElementById('awc-qr-status').textContent = '等待扫码...';
      _showQR();
      _startPolling(data.qr_id);
    } catch (e) {
      if (window.Toast) Toast.show(e.message || '生成失败', 'error');
      btn.disabled = false;
      btn.textContent = '生成微信二维码';
    }
  }

  // ── QR status polling ──

  function _startPolling(qrUrl) {
    _stopPolling();
    _pollTimer = setInterval(() => _pollStatus(qrUrl), 2500);
  }

  function _stopPolling() {
    if (_pollTimer) {
      clearInterval(_pollTimer);
      _pollTimer = null;
    }
  }

  async function _pollStatus(qrUrl) {
    try {
      const data = await API.request('GET', `/admin/wechat/qrcode/status?qrcode=${encodeURIComponent(qrUrl)}`);
      const statusEl = document.getElementById('awc-qr-status');
      if (data.status === 'scanned') {
        statusEl.textContent = '已扫码，请在手机上确认...';
        statusEl.style.color = 'var(--primary)';
      } else if (data.status === 'confirmed') {
        _stopPolling();
        statusEl.textContent = '连接成功！';
        statusEl.style.color = '#07c160';
        setTimeout(() => _checkSession(), 500);
      } else if (data.status === 'expired') {
        _stopPolling();
        statusEl.textContent = '二维码已过期，请重新生成';
        statusEl.style.color = 'var(--danger)';
        setTimeout(() => _showDisconnected(), 1500);
      }
    } catch {
      // ignore poll errors
    }
  }

  // ── disconnect ──

  async function _disconnect() {
    if (!confirm('确定断开微信连接？')) return;
    try {
      await API.request('DELETE', '/admin/wechat/session');
      _showDisconnected();
      if (window.Toast) Toast.show('微信已断开');
    } catch (e) {
      if (window.Toast) Toast.show(e.message || '断开失败', 'error');
    }
  }

  // ── messages ──

  async function _loadMessages() {
    const container = document.getElementById('awc-messages');
    if (!container) return;
    try {
      const data = await API.request('GET', '/admin/wechat/messages');
      const msgs = data.messages || [];
      if (!msgs.length) {
        container.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-muted);font-size:13px">暂无消息</div>';
        return;
      }
      container.innerHTML = '<div style="display:flex;flex-direction:column;gap:6px">' +
        msgs.map(m => {
          const cls = m.role === 'user' ? 'user' : 'assistant';
          const time = (m.timestamp || '').slice(11, 19);
          return `<div class="awc-msg-bubble ${cls}">${esc(m.content || '')}<span class="awc-time">${time}</span></div>`;
        }).join('') + '</div>';
      container.scrollTop = container.scrollHeight;
    } catch {
      container.innerHTML = '<div style="padding:8px;color:var(--danger);font-size:13px">加载失败</div>';
    }
  }

  // ── init ──

  function init() {
    const btn = document.getElementById('btn-admin-wechat');
    if (btn) btn.addEventListener('click', show);

    const closeBtn = document.getElementById('btn-close-awc');
    if (closeBtn) closeBtn.addEventListener('click', hide);

    const genBtn = document.getElementById('btn-awc-gen-qr');
    if (genBtn) genBtn.addEventListener('click', _generateQR);

    const discBtn = document.getElementById('btn-awc-disconnect');
    if (discBtn) discBtn.addEventListener('click', _disconnect);

    const refBtn = document.getElementById('btn-awc-refresh-msgs');
    if (refBtn) refBtn.addEventListener('click', _loadMessages);

    overlay()?.addEventListener('click', (e) => {
      if (e.target === overlay()) hide();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  return { show, hide };
})();
