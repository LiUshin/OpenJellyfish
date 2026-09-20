import { useState } from 'react';
import { Form, Input, Button, Alert } from 'antd';
import { ArrowRight } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../stores/authContext';
import LanguageSwitcher from '../components/LanguageSwitcher';
import styles from './Login.module.css';

export default function Login() {
  const { login, register } = useAuth();
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState('login');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function submit(values: { username: string; password: string; reg_key?: string }) {
    if (loading) return;
    setLoading(true);
    setError('');
    try {
      if (activeTab === 'login') await login(values.username, values.password);
      else await register(values.username, values.password, values.reg_key || '');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t(activeTab === 'login' ? 'login.loginFailed' : 'login.registerFailed'));
    } finally {
      setLoading(false);
    }
  }
  const isLogin = activeTab === 'login';
  return <main className={styles.root}>
    <header className={styles.header}>
      <a href="/" className={styles.brand}><img src="/media_resources/jellyfishlogo.png" width="36" height="36" alt="" />OpenJellyfish</a>
      <LanguageSwitcher variant="icon" placement="bottom" syncBackend={false} />
    </header>
    <section className={styles.story} aria-labelledby="brand-heading">
      <div className={styles.orbit} aria-hidden="true"><img src="/media_resources/jellyfishlogo.png" width="220" height="220" alt="" /><span>OPENJELLYFISH / WORKSPACE</span></div>
      <h1 id="brand-heading">{t('ux.brandHeading')}</h1>
      <p>{t('ux.brandDescription')}</p>
      <ol className={styles.steps}>{[0, 1, 2].map(i => <li key={i}><span>0{i + 1}</span>{t(`ux.brandSteps.${i}`)}</li>)}</ol>
    </section>
    <section className={styles.formRegion} aria-labelledby="login-heading">
      <div className={styles.card}>
        <h2 id="login-heading">{t(isLogin ? 'ux.loginTitle' : 'ux.registerTitle')}</h2>
        <p className={styles.description}>{t(isLogin ? 'ux.loginDescription' : 'ux.registerDescription')}</p>
        <div className={styles.tabs} role="tablist" aria-label={t('ux.loginTitle')}>
          {['login', 'register'].map(key => <button key={key} type="button" role="tab" id={`auth-tab-${key}`}
            aria-controls={`auth-panel-${key}`} aria-selected={activeTab === key} tabIndex={activeTab === key ? 0 : -1}
            disabled={loading} onClick={() => { setActiveTab(key); setError(''); }}
            onKeyDown={event => {
              if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
              event.preventDefault();
              const next = event.key === 'Home' ? 'login' : event.key === 'End' ? 'register' : key === 'login' ? 'register' : 'login';
              setActiveTab(next); setError('');
              document.getElementById(`auth-tab-${next}`)?.focus();
            }}>{t(`login.${key}Title`)}</button>)}
        </div>
        <div role="tabpanel" id={`auth-panel-${activeTab}`} aria-labelledby={`auth-tab-${activeTab}`}>
        {error && <Alert role="alert" message={error} type="error" showIcon className={styles.error} />}
        <Form key={activeTab} name={`auth-${activeTab}`} onFinish={submit} layout="vertical" requiredMark={false} disabled={loading}>
          {!isLogin && <Form.Item name="reg_key" label={t('ux.regKey')} extra={t('ux.regHelp')}
            rules={[{ required: true, message: t('login.regKeyRequired') }]}>
            <Input autoComplete="off" placeholder={t('login.regKeyPlaceholder')} size="large" />
          </Form.Item>}
          <Form.Item name="username" label={t('login.username')} rules={[{ required: true, min: isLogin ? 1 : 2, message: t(isLogin ? 'login.usernameRequired' : 'login.usernameMinLen') }]}>
            <Input autoComplete="username" autoCapitalize="none" spellCheck={false} size="large" />
          </Form.Item>
          <Form.Item name="password" label={t('login.password')} rules={[{ required: true, min: isLogin ? 1 : 4, message: t(isLogin ? 'login.passwordRequired' : 'login.passwordMinLen') }]}>
            <Input.Password autoComplete={isLogin ? 'current-password' : 'new-password'} size="large" />
          </Form.Item>
          <Button className={styles.submit} type="primary" htmlType="submit" block loading={loading} icon={<ArrowRight size={18} />} iconPosition="end">
            {t(isLogin ? 'login.loginBtn' : 'login.registerBtn')}
          </Button>
        </Form>
        </div>
        <p className={styles.footnote}>{t('ux.loginFootnote')}</p>
      </div>
    </section>
  </main>;
}
