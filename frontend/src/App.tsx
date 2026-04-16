import { ConfigProvider, App as AntdApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { AuthProvider } from './stores/authContext';
import { StreamProvider } from './stores/streamContext';
import { ThemeProvider, useTheme } from './stores/themeContext';
import { FileWorkspaceProvider } from './stores/fileWorkspaceContext';
import AppRouter from './router';

function ThemedApp() {
  const { antdConfig } = useTheme();
  return (
    <ConfigProvider theme={antdConfig} locale={zhCN}>
      <AntdApp>
        <AuthProvider>
          <StreamProvider>
            <FileWorkspaceProvider>
              <AppRouter />
            </FileWorkspaceProvider>
          </StreamProvider>
        </AuthProvider>
      </AntdApp>
    </ConfigProvider>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <ThemedApp />
    </ThemeProvider>
  );
}
