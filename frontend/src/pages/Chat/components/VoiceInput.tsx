import { useState, useRef, useCallback, useEffect } from 'react';
import { Button, Tooltip, message } from 'antd';
import { AudioOutlined, LoadingOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import * as api from '../../../services/api';
import styles from '../chat.module.css';

// 录音过短的下限（toggle 模式下也保留，防止误点立刻松开）
const MIN_DURATION_MS = 500;

interface VoiceInputProps {
  onTranscript: (text: string) => void;
  disabled?: boolean;
}

export default function VoiceInput({ onTranscript, disabled }: VoiceInputProps) {
  const { t } = useTranslation();
  const [recording, setRecording] = useState(false);
  const [duration, setDuration] = useState(0);
  const [transcribing, setTranscribing] = useState(false);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startTimeRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval>>(0 as unknown as ReturnType<typeof setInterval>);
  const streamRef = useRef<MediaStream | null>(null);
  const cancelledRef = useRef(false);

  const cleanup = useCallback(() => {
    clearInterval(timerRef.current);
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    mediaRecorderRef.current = null;
    chunksRef.current = [];
  }, []);

  useEffect(() => () => cleanup(), [cleanup]);

  const startRecording = useCallback(async () => {
    if (recording || disabled || transcribing) return;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm';

      const recorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = recorder;
      chunksRef.current = [];
      cancelledRef.current = false;

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.start(100);
      startTimeRef.current = Date.now();
      setRecording(true);
      setDuration(0);

      timerRef.current = setInterval(() => {
        setDuration(Math.floor((Date.now() - startTimeRef.current) / 1000));
      }, 200);
    } catch {
      message.error(t('voice.permissionFail'));
    }
  }, [recording, disabled, transcribing, t]);

  const stopRecording = useCallback(async () => {
    if (!recording || !mediaRecorderRef.current) return;

    const elapsed = Date.now() - startTimeRef.current;
    const cancelled = cancelledRef.current;
    clearInterval(timerRef.current);
    setRecording(false);

    if (elapsed < MIN_DURATION_MS) {
      cleanup();
      if (!cancelled) message.info(t('voice.tooShort'));
      return;
    }

    const recorder = mediaRecorderRef.current;

    const blob = await new Promise<Blob>((resolve) => {
      recorder.onstop = () => {
        resolve(new Blob(chunksRef.current, { type: recorder.mimeType }));
      };
      recorder.stop();
    });

    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;

    if (cancelled) {
      cleanup();
      message.info(t('voice.cancelled'));
      return;
    }

    setTranscribing(true);
    try {
      const { text } = await api.transcribeAudio(blob, 'recording.webm');
      if (text.trim()) {
        onTranscript(text.trim());
      } else {
        message.info(t('voice.noVoice'));
      }
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('voice.transcribeFail'));
    } finally {
      setTranscribing(false);
      cleanup();
    }
  }, [recording, cleanup, onTranscript, t]);

  const cancelRecording = useCallback(() => {
    if (!recording) return;
    cancelledRef.current = true;
    stopRecording();
  }, [recording, stopRecording]);

  const toggleRecording = useCallback(() => {
    if (transcribing || disabled) return;
    if (recording) {
      stopRecording();
    } else {
      startRecording();
    }
  }, [recording, transcribing, disabled, startRecording, stopRecording]);

  // 仅录音中绑 Esc 取消（避免与 chat 输入框 Enter 发送等其他键冲突）。
  // 开始/停止录音统一通过点按钮，不绑全局开始快捷键，保持 UX 干净。
  useEffect(() => {
    if (!recording) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        cancelRecording();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [recording, cancelRecording]);

  const formatDuration = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${sec.toString().padStart(2, '0')}`;
  };

  if (transcribing) {
    return (
      <Button
        type="text"
        size="small"
        className={styles.voiceBtn}
        disabled
        icon={<LoadingOutlined spin />}
      />
    );
  }

  const tooltipText = recording
    ? t('voice.tipRecording')
    : t('voice.tipStart');

  return (
    <Tooltip title={tooltipText} mouseEnterDelay={0.4}>
      <Button
        type="text"
        size="small"
        className={`${styles.voiceBtn} ${recording ? styles.voiceBtnActive : ''}`}
        onClick={toggleRecording}
        disabled={disabled}
        aria-label={recording ? t('voice.ariaStop') : t('voice.ariaStart')}
        aria-pressed={recording}
        icon={
          recording ? (
            <span className={styles.voiceRecording}>
              <span className={styles.voicePulse} />
              <span className={styles.voiceDuration}>{formatDuration(duration)}</span>
            </span>
          ) : (
            <AudioOutlined />
          )
        }
      />
    </Tooltip>
  );
}
