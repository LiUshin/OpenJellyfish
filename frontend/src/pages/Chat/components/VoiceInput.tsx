import { useState, useRef, useCallback, useEffect } from 'react';
import { Button, message } from 'antd';
import { AudioOutlined, LoadingOutlined } from '@ant-design/icons';
import * as api from '../../../services/api';
import styles from '../chat.module.css';

const MIN_DURATION_MS = 500;

interface VoiceInputProps {
  onTranscript: (text: string) => void;
  disabled?: boolean;
}

export default function VoiceInput({ onTranscript, disabled }: VoiceInputProps) {
  const [recording, setRecording] = useState(false);
  const [duration, setDuration] = useState(0);
  const [transcribing, setTranscribing] = useState(false);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startTimeRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval>>(0 as unknown as ReturnType<typeof setInterval>);
  const streamRef = useRef<MediaStream | null>(null);

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
      message.error('无法访问麦克风，请检查权限设置');
    }
  }, [recording, disabled, transcribing]);

  const stopRecording = useCallback(async () => {
    if (!recording || !mediaRecorderRef.current) return;

    const elapsed = Date.now() - startTimeRef.current;
    clearInterval(timerRef.current);
    setRecording(false);

    if (elapsed < MIN_DURATION_MS) {
      cleanup();
      message.info('录音太短，请按住按钮说话');
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

    setTranscribing(true);
    try {
      const { text } = await api.transcribeAudio(blob, 'recording.webm');
      if (text.trim()) {
        onTranscript(text.trim());
      } else {
        message.info('未识别到语音内容');
      }
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '语音识别失败');
    } finally {
      setTranscribing(false);
      cleanup();
    }
  }, [recording, cleanup, onTranscript]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Tab' && !e.repeat) {
        e.preventDefault();
        startRecording();
      }
    };
    const handleKeyUp = (e: KeyboardEvent) => {
      if (e.key === 'Tab') {
        e.preventDefault();
        stopRecording();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('keyup', handleKeyUp);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('keyup', handleKeyUp);
    };
  }, [startRecording, stopRecording]);

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

  return (
    <Button
      type="text"
      size="small"
      className={`${styles.voiceBtn} ${recording ? styles.voiceBtnActive : ''}`}
      onMouseDown={(e) => {
        e.preventDefault();
        startRecording();
      }}
      onMouseUp={stopRecording}
      onMouseLeave={() => {
        if (recording) stopRecording();
      }}
      onTouchStart={(e) => {
        e.preventDefault();
        startRecording();
      }}
      onTouchEnd={stopRecording}
      disabled={disabled}
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
  );
}
