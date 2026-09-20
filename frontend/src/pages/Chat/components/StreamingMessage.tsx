import { memo } from 'react';
import AssistantResponseBody, { type ResponseBodyProps } from './AssistantResponseBody';
import styles from '../chat.module.css';
export type { ToolRendererProps } from './AssistantResponseBody';

interface Props extends ResponseBodyProps { avatarSrc?: string }

function StreamingMessage({ avatarSrc = '/media_resources/jellyfishlogo.png', ...props }: Props) {
  return <div className={styles.messageBubble}>
    <div className={styles.messageAvatar} data-role="assistant">
      <img src={avatarSrc} alt="" width={32} height={32} style={{ display: 'block', borderRadius: 'inherit', objectFit: 'cover' }} />
    </div>
    <div className={styles.messageBody}><AssistantResponseBody {...props} /></div>
  </div>;
}
export default memo(StreamingMessage);
