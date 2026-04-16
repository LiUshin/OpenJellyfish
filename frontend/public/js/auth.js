/**
 * 认证模块 - 登录/注册页面逻辑
 */

const Auth = (() => {
  let onLoginSuccess = null;

  function init(callback) {
    onLoginSuccess = callback;

    // Tab 切换
    document.querySelectorAll('.auth-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');

        const isLogin = tab.dataset.tab === 'login';
        document.getElementById('login-form').style.display = isLogin ? 'block' : 'none';
        document.getElementById('register-form').style.display = isLogin ? 'none' : 'block';
        hideError();
      });
    });

    // 登录表单
    document.getElementById('login-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const username = document.getElementById('login-username').value.trim();
      const password = document.getElementById('login-password').value;

      if (!username || !password) {
        showError('请填写用户名和密码');
        return;
      }

      try {
        const result = await API.login(username, password);
        API.setToken(result.token);
        localStorage.setItem('user_id', result.user_id);
        localStorage.setItem('username', result.username);
        onLoginSuccess?.(result);
      } catch (err) {
        showError(err.message);
      }
    });

    // 注册表单
    document.getElementById('register-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const regKey = document.getElementById('reg-key').value.trim();
      const username = document.getElementById('reg-username').value.trim();
      const password = document.getElementById('reg-password').value;

      if (!regKey) {
        showError('请输入注册码');
        return;
      }
      if (!username || !password) {
        showError('请填写用户名和密码');
        return;
      }

      try {
        const result = await API.register(username, password, regKey);
        API.setToken(result.token);
        localStorage.setItem('user_id', result.user_id);
        localStorage.setItem('username', result.username);
        onLoginSuccess?.(result);
      } catch (err) {
        showError(err.message);
      }
    });
  }

  function showError(msg) {
    const el = document.getElementById('auth-error');
    el.textContent = msg;
    el.style.display = 'block';
  }

  function hideError() {
    document.getElementById('auth-error').style.display = 'none';
  }

  /** 检查是否已登录 */
  async function checkSession() {
    const token = API.getToken();
    if (!token) return null;

    try {
      const user = await API.getMe();
      return user;
    } catch {
      API.clearToken();
      return null;
    }
  }

  function logout() {
    API.clearToken();
    localStorage.removeItem('user_id');
    localStorage.removeItem('username');
    window.location.reload();
  }

  return { init, checkSession, logout };
})();
