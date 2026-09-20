import { lazy, Suspense } from 'react';
import RoutePending from '../components/RoutePending';
import ErrorBoundary from '../components/ErrorBoundary';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../stores/authContext';
const AppLayout = lazy(() => import('../layouts/AppLayout'));
const Login = lazy(() => import('../pages/Login'));
const ChatPage = lazy(() => import('../pages/Chat'));
const SettingsLayout = lazy(() => import('../pages/Settings'));
const PromptPage = lazy(() => import('../pages/Settings/PromptPage'));
const SubagentPage = lazy(() => import('../pages/Settings/SubagentPage'));
const PackagesPage = lazy(() => import('../pages/Settings/PackagesPage'));
const AdminServicesPage = lazy(() => import('../pages/AdminServices'));
const SchedulerPage = lazy(() => import('../pages/Scheduler'));
const WeChatPage = lazy(() => import('../pages/WeChat'));
const InboxPage = lazy(() => import('../pages/Settings/InboxPage'));
const GeneralPage = lazy(() => import('../pages/Settings/GeneralPage'));
const BackupPage = lazy(() => import('../pages/Settings/BackupPage'));
const VoicePage = lazy(() => import('../pages/Settings/VoicePage'));
const UsagePage = lazy(() => import('../pages/Settings/UsagePage'));
const RuntimePilot = lazy(() => import('../pages/RuntimePilot'));

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return <RoutePending />;

  if (!user) return <Navigate to="/login" state={{ from: location.pathname + location.search + location.hash }} replace />;
  return <>{children}</>;
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const from = location.state?.from;
  const destination = typeof from === "string" && from.startsWith("/") && !from.startsWith("//") && !from.startsWith("/login") ? from : "/";
  const { user, loading } = useAuth();

  if (loading) return <RoutePending />;

  if (user) return <Navigate to={destination} replace />;
  return <>{children}</>;
}

export default function AppRouter() {
  return (
      <Suspense fallback={<RoutePending />}><Routes>
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
              <ErrorBoundary scope="app-layout">
                <AppLayout />
              </ErrorBoundary>
            </ProtectedRoute>
          }
        >
          <Route
            path="/"
            element={
              <ErrorBoundary scope="chat">
                <ChatPage />
              </ErrorBoundary>
            }
          />
          <Route
            path="/settings"
            element={
              <ErrorBoundary scope="settings">
                <SettingsLayout />
              </ErrorBoundary>
            }
          >
            <Route index element={<Navigate to="/settings/prompt" replace />} />
            <Route path="prompt" element={<PromptPage />} />
            <Route path="subagents" element={<SubagentPage />} />
            <Route path="packages" element={<PackagesPage />} />
            <Route path="batch" element={<Navigate to="/settings/general" replace />} />
            <Route path="services" element={<AdminServicesPage />} />
            <Route path="scheduler" element={<SchedulerPage />} />
            <Route path="wechat" element={<WeChatPage />} />
            <Route path="voice" element={<VoicePage />} />
            <Route path="inbox" element={<InboxPage />} />
            <Route path="usage" element={<UsagePage />} />
            <Route path="general" element={<GeneralPage />} />
            <Route path="backup" element={<BackupPage />} />
          </Route>
          <Route path="/runtime-pilot" element={<ErrorBoundary scope="runtime-pilot"><RuntimePilot /></ErrorBoundary>} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes></Suspense>
  );
}
