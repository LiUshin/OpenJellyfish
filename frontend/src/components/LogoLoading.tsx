const LOADING_SRC = '/media_resources/loadingGif.webm';

interface LogoLoadingProps {
  size?: number;
  text?: string;
}

export default function LogoLoading({ size = 280, text }: LogoLoadingProps) {
  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: '40vh',
      gap: 12,
    }}>
      <video
        src={LOADING_SRC}
        autoPlay
        loop
        muted
        playsInline
        width={size}
        height={size}
        style={{ objectFit: 'contain' }}
      />
      {text && (
        <span style={{ color: 'var(--jf-text-dim)', fontSize: 13 }}>{text}</span>
      )}
    </div>
  );
}
