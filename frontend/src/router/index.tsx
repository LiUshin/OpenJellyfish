import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useAuth } from '../stores/authContext';
import AppLayout from '../layouts/AppLayout';
import Login from '../pages/Login';
import ChatPage from '../pages/Chat';
import SettingsLayout from '../pages/Settings';
import PromptPage from '../pages/Settings/PromptPage';
import SubagentPage from '../pages/Settings/SubagentPage';
import PackagesPage from '../pages/Settings/PackagesPage';
import AdminServicesPage from '../pages/AdminServices';
import SchedulerPage from '../pages/Scheduler';
import WeChatPage from '../pages/WeChat';
import InboxPage from '../pages/Settings/InboxPage';
import GeneralPage from '../pages/Settings/GeneralPage';
import { Spin } from 'antd';

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div
        style={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: '100vh',
          background: 'var(--jf-bg-deep)',
        }}
      >
        <Spin size="large" />
      </div>
    );
  }

  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div
        style={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: '100vh',
          background: 'var(--jf-bg-deep)',
        }}
      >
        <Spin size="large" />
      </div>
    );
  }

  if (user) return <Navigate to="/" replace />;
  return <>{children}</>;
}

export default function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/login"
          element={
            <PublicRoute>
              <Login />
            </PublicRoute>
          }
        />
        <Route
          element={
            <ProtectedRoute>
              <AppLayout />
            </ProtectedRoute>
          }
        >
          <Route path="/" element={<ChatPage />} />
          <Route path="/settings" element={<SettingsLayout />}>
            <Route index element={<Navigate to="/settings/prompt" replace />} />
            <Route path="prompt" element={<PromptPage />} />
            <Route path="subagents" element={<SubagentPage />} />
            <Route path="packages" element={<PackagesPage />} />
            <Route path="batch" element={<Navigate to="/settings/general" replace />} />
            <Route path="services" element={<AdminServicesPage />} />
            <Route path="scheduler" element={<SchedulerPage />} />
            <Route path="wechat" element={<WeChatPage />} />
            <Route path="inbox" element={<InboxPage />} />
            <Route path="general" element={<GeneralPage />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
