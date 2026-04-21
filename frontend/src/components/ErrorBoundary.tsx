import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Result, Button, Collapse, Space, message } from 'antd';
import { ArrowClockwise, Copy, House } from '@phosphor-icons/react';

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Logical name of the region this boundary guards; shown in error report */
  scope?: string;
  /** Custom fallback renderer; receives error + reset callback */
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

/**
 * React Error Boundary — catches render/lifecycle errors in children and
 * renders a friendly fallback instead of white-screening the app.
 *
 * Note: must remain a class component (React 19 still requires class-based
 * boundaries; no hook equivalent exists). This is the single class in the
 * codebase and intentional.
 */
export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null, errorInfo: null };

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    this.setState({ errorInfo });
    const scope = this.props.scope || 'app';
    console.error(`[ErrorBoundary:${scope}]`, error, errorInfo);
  }

  reset = () => {
    this.setState({ error: null, errorInfo: null });
  };

  reload = () => {
    window.location.reload();
  };

  goHome = () => {
    window.location.href = '/';
  };

  copyReport = async () => {
    const { error, errorInfo } = this.state;
    const scope = this.props.scope || 'app';
    const report = [
      `Scope: ${scope}`,
      `Time: ${new Date().toISOString()}`,
      `URL: ${window.location.href}`,
      `UserAgent: ${navigator.userAgent}`,
      '',
      `Error: ${error?.name}: ${error?.message}`,
      '',
      'Stack:',
      error?.stack || '(no stack)',
      '',
      'Component Stack:',
      errorInfo?.componentStack || '(no component stack)',
    ].join('\n');

    try {
      await navigator.clipboard.writeText(report);
      message.success('错误信息已复制，请发送给管理员');
    } catch {
      message.error('复制失败，请手动选中下方文字');
    }
  };

  render() {
    const { error, errorInfo } = this.state;
    if (!error) return this.props.children;

    if (this.props.fallback) {
      return this.props.fallback(error, this.reset);
    }

    const detailText = [
      `${error.name}: ${error.message}`,
      '',
      error.stack || '',
      '',
      errorInfo?.componentStack || '',
    ].join('\n');

    return (
      <div style={{ padding: 24, minHeight: '60vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Result
          status="error"
          title="页面出错了"
          subTitle="抱歉，这块功能遇到了意外问题。你可以尝试刷新或回到首页，如果反复出现，请把错误信息复制给管理员。"
          extra={
            <Space wrap>
              <Button type="primary" icon={<ArrowClockwise size={16} />} onClick={this.reload}>
                刷新页面
              </Button>
              <Button icon={<House size={16} />} onClick={this.goHome}>
                回到首页
              </Button>
              <Button icon={<Copy size={16} />} onClick={this.copyReport}>
                复制错误信息
              </Button>
            </Space>
          }
        >
          <Collapse
            size="small"
            items={[
              {
                key: 'detail',
                label: '技术细节（发给管理员时请一并附上）',
                children: (
                  <pre
                    style={{
                      maxHeight: 320,
                      overflow: 'auto',
                      fontSize: 12,
                      margin: 0,
                      padding: 12,
                      background: 'var(--jf-bg-deep, #1a1a1a)',
                      color: 'var(--jf-text-dim, #bbb)',
                      border: '1px solid var(--jf-border, #333)',
                      borderRadius: 4,
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                    }}
                  >
                    {detailText}
                  </pre>
                ),
              },
            ]}
          />
        </Result>
      </div>
    );
  }
}
