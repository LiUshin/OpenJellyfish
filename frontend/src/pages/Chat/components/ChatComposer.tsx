import type { ReactNode } from 'react';
import { Button, Tooltip } from 'antd';
import { Paperclip, PaperPlaneRight, Stop } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import VoiceInput from './VoiceInput';
import styles from '../chat.module.css';

interface ChatComposerProps {
  input: ReactNode;
  attachments?: ReactNode;
  tools?: ReactNode;
  model: ReactNode;
  hint?: ReactNode;
  onUpload: () => void;
  uploadDisabled?: boolean;
  hasAttachments?: boolean;
  onTranscript: (text: string) => void;
  voiceDisabled?: boolean;
  onSend: () => void;
  sendDisabled?: boolean;
  sending?: boolean;
  onStop?: () => void;
  stopDisabled?: boolean;
}

/** Shared interaction and layout for API, Codex and Cursor conversations.
 * Each engine supplies supported tools and its existing send/stop handlers. */
export default function ChatComposer({ input, attachments, tools, model, hint, onUpload, uploadDisabled,
  hasAttachments, onTranscript, voiceDisabled, onSend, sendDisabled, sending, onStop, stopDisabled }: ChatComposerProps) {
  const { t } = useTranslation();
  return <div data-chat-composer>
    <div className={styles.composerCard}>
      {attachments}
      <div className={styles.inputToolbar}>
        <div className={styles.composerTools}>
          <Tooltip title={t('chat.addAttachment')}><button type="button"
            className={`${styles.capBtn} ${hasAttachments ? styles.capBtnActive : ''}`}
            aria-label={t('chat.addAttachment')} onClick={onUpload} disabled={uploadDisabled}>
            <Paperclip size={16} />
          </button></Tooltip>
          {tools}
        </div>
        <div className={styles.composerModel}>{model}</div>
      </div>
      <div className={styles.inputWrapper}>
        <VoiceInput onTranscript={onTranscript} disabled={voiceDisabled} />
        {input}
        <div className={styles.composerSend}>
          {onStop && <Tooltip title={t('chat.stopGeneration')}><Button danger type="primary"
            icon={<Stop size={18} weight="fill" />} aria-label={t('chat.stopGeneration')}
            onClick={onStop} disabled={stopDisabled} /></Tooltip>}
          <Tooltip title={t('chat.sendMessage')}><Button type="primary"
            icon={<PaperPlaneRight size={18} weight="fill" />} aria-label={t('chat.sendMessage')}
            onClick={onSend} disabled={sendDisabled} loading={sending} /></Tooltip>
        </div>
      </div>
    </div>
    <div className={styles.composerHint}>{hint ?? t('chat.composeHint')}</div>
  </div>;
}
