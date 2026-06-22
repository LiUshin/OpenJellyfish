import { useCallback, useEffect, useRef, useState } from 'react';
import { Modal, Button, Tag, Space } from 'antd';
import { Microphone, MicrophoneSlash, PhoneX } from '@phosphor-icons/react';
import {
  Room,
  RoomEvent,
  Track,
  type RemoteTrack,
  type RemoteTrackPublication,
  type RemoteParticipant,
} from 'livekit-client';
import * as api from '../services/api';

type CallState = 'idle' | 'connecting' | 'connected' | 'error';

interface Props {
  open: boolean;
  conversationId: string;
  model?: string;
  onClose: () => void;
}

/**
 * 实时语音通话弹窗。加入 LiveKit room、发布麦克风、播放 agent 音频。
 *
 * 桥接令牌由后端在签发 LiveKit 令牌时塞进参与者 metadata,语音 Worker 读取后
 * 回调 Core 引导与委派 —— 前端无需关心。
 */
export default function VoiceCallModal({ open, conversationId, model, onClose }: Props) {
  const [state, setState] = useState<CallState>('idle');
  const [error, setError] = useState('');
  const [muted, setMuted] = useState(false);
  const [agentSpeaking, setAgentSpeaking] = useState(false);
  const roomRef = useRef<Room | null>(null);
  const audioElsRef = useRef<HTMLMediaElement[]>([]);

  const cleanup = useCallback(async () => {
    const room = roomRef.current;
    roomRef.current = null;
    if (room) {
      try { await room.disconnect(); } catch { /* noop */ }
    }
    audioElsRef.current.forEach((el) => { try { el.remove(); } catch { /* noop */ } });
    audioElsRef.current = [];
    setAgentSpeaking(false);
  }, []);

  const connect = useCallback(async () => {
    setState('connecting');
    setError('');
    try {
      const { url, token } = await api.getVoiceLiveToken(conversationId, model);
      const room = new Room({ adaptiveStream: true, dynacast: true });
      roomRef.current = room;

      room.on(RoomEvent.TrackSubscribed, (track: RemoteTrack, _pub: RemoteTrackPublication, _p: RemoteParticipant) => {
        if (track.kind === Track.Kind.Audio) {
          const el = track.attach();
          el.autoplay = true;
          document.body.appendChild(el);
          audioElsRef.current.push(el);
        }
      });
      room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        // agent 是远端参与者:有任意远端在说话即视为 agent 在讲。
        setAgentSpeaking(speakers.some((s) => s.identity !== room.localParticipant.identity));
      });
      room.on(RoomEvent.Disconnected, () => {
        setState('idle');
      });

      await room.connect(url, token);
      await room.localParticipant.setMicrophoneEnabled(true);
      setMuted(false);
      setState('connected');
    } catch (e) {
      setError((e as Error).message || String(e));
      setState('error');
      await cleanup();
    }
  }, [conversationId, model, cleanup]);

  // 打开即连,关闭即清理。
  useEffect(() => {
    if (open) {
      void connect();
    }
    return () => { void cleanup(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const toggleMute = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    const next = !muted;
    await room.localParticipant.setMicrophoneEnabled(!next);
    setMuted(next);
  }, [muted]);

  const hangUp = useCallback(async () => {
    await cleanup();
    setState('idle');
    onClose();
  }, [cleanup, onClose]);

  const statusTag = () => {
    if (state === 'connecting') return <Tag color="processing">连接中…</Tag>;
    if (state === 'error') return <Tag color="error">连接失败</Tag>;
    if (state === 'connected') {
      return agentSpeaking
        ? <Tag color="green">助手讲话中…</Tag>
        : <Tag color="blue">聆听中…</Tag>;
    }
    return <Tag>未连接</Tag>;
  };

  return (
    <Modal
      open={open}
      onCancel={hangUp}
      footer={null}
      title="语音通话"
      width={360}
      destroyOnClose
      maskClosable={false}
    >
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 20, padding: '12px 0' }}>
        <div
          style={{
            width: 96, height: 96, borderRadius: '50%',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: agentSpeaking ? 'var(--jf-success, #52c41a)' : 'var(--jf-accent, #1677ff)',
            color: '#fff',
            transition: 'transform .2s, background .2s',
            transform: agentSpeaking ? 'scale(1.06)' : 'scale(1)',
          }}
        >
          <Microphone size={40} weight="fill" />
        </div>

        {statusTag()}
        {error && <div style={{ color: 'var(--jf-error, #ff4d4f)', fontSize: 12, textAlign: 'center' }}>{error}</div>}

        <Space size="large">
          <Button
            shape="circle"
            size="large"
            onClick={toggleMute}
            disabled={state !== 'connected'}
            icon={muted ? <MicrophoneSlash size={20} /> : <Microphone size={20} />}
          />
          <Button
            shape="circle"
            size="large"
            danger
            type="primary"
            onClick={hangUp}
            icon={<PhoneX size={20} />}
          />
        </Space>
      </div>
    </Modal>
  );
}
