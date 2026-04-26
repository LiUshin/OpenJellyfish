/**
 * FileTokenInput — contenteditable-based chat input that renders [[FILE:/path]] tokens
 * as inline chip cards. Key design choices:
 *
 * - `value` (plain text with [[FILE:...]] markers) is the source of truth.
 * - DOM is rebuilt from `value` only when it changes externally (via `internalValueRef`).
 * - User edits fire DOM→string serialisation via `syncToParent`, which updates
 *   `internalValueRef` before calling `onChange` to break the feedback loop.
 * - Chips are `contentEditable="false"` spans → browser treats them as atoms.
 *   A single Backspace when caret is immediately after a chip removes it wholesale.
 * - Chip `×` button (left side, visible on hover) removes the chip on mousedown.
 * - Chip body click → revealInBrowser (open file in FilePanel), handled via the
 *   global [data-jf-file] click delegate already wired in fileWorkspaceContext.
 * - `Enter` → onSend; `Shift+Enter` → insert newline.
 * - `@` trigger detection runs on every input event; parent controls mention picker state.
 * - Image paste → onImagePaste; text paste → plain text only (strip HTML).
 */

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  type ClipboardEvent as RClipboardEvent,
} from 'react';
import styles from '../chat.module.css';

// ── Serialisation helpers ─────────────────────────────────────────────────────

const CHIP_RE = /\[\[FILE:([^\]]+)\]\]/g;

/** Walk the flat DOM of the contenteditable root and produce the plain-text
 *  serialisation where every chip becomes `[[FILE:/path]]`. */
function serializeDom(root: HTMLElement): string {
  let text = '';
  for (const node of root.childNodes) {
    if (node.nodeType === Node.TEXT_NODE) {
      text += node.textContent ?? '';
    } else if (node instanceof HTMLElement && node.dataset.mentionPath) {
      text += `[[FILE:${node.dataset.mentionPath}]]`;
    } else if (node instanceof HTMLBRElement) {
      text += '\n';
    }
    // Other nodes (e.g. stray spans from browser auto-wrapping) are ignored.
  }
  return text;
}

/** Rebuild the DOM from a plain-text serialised value. Replaces all children. */
function hydrateFromText(
  root: HTMLElement,
  text: string,
  onDelete: (chipNode: HTMLElement) => void,
): void {
  root.innerHTML = '';
  CHIP_RE.lastIndex = 0;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = CHIP_RE.exec(text)) !== null) {
    if (m.index > last) {
      root.appendChild(document.createTextNode(text.slice(last, m.index)));
    }
    root.appendChild(createChipNode(m[1], onDelete));
    last = CHIP_RE.lastIndex;
  }
  if (last < text.length) {
    root.appendChild(document.createTextNode(text.slice(last)));
  }
}

/** Create a chip DOM node for a single [[FILE:/path]] token. */
function createChipNode(
  path: string,
  onDelete: (node: HTMLElement) => void,
): HTMLElement {
  const isDir = path.endsWith('/');
  const name = path.replace(/\/$/, '').split('/').pop() || path;

  const span = document.createElement('span');
  span.className = 'jf-token-chip';
  span.dataset.mentionPath = path;
  span.contentEditable = 'false';
  span.setAttribute('data-jf-file', path);
  if (isDir) span.setAttribute('data-jf-is-dir', 'true');
  span.title = `${path} · 点击在文件浏览器打开`;

  // × button — left side, visible on hover via CSS
  const xBtn = document.createElement('button');
  xBtn.className = 'jf-token-chip-x';
  xBtn.type = 'button';
  xBtn.tabIndex = -1;
  xBtn.textContent = '×';
  xBtn.title = '删除引用';
  xBtn.addEventListener('mousedown', (e) => {
    // mousedown not click so it fires before the contenteditable loses focus
    e.preventDefault();
    e.stopPropagation();
    onDelete(span);
  });

  const icon = document.createElement('span');
  icon.className = 'jf-token-chip-icon';
  icon.textContent = isDir ? '📁' : '📄';
  icon.setAttribute('aria-hidden', 'true');

  const label = document.createElement('span');
  label.className = 'jf-token-chip-name';
  label.textContent = name + (isDir ? '/' : '');

  span.appendChild(xBtn);
  span.appendChild(icon);
  span.appendChild(label);
  return span;
}

// ── Caret helpers ─────────────────────────────────────────────────────────────

/** Get cursor position as character offset in the serialised text string.
 *  Uses Range.cloneContents trick: clone root→caret into a temp div and serialise. */
function getCaretOffset(root: HTMLElement): number {
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount || !root.contains(sel.anchorNode)) return 0;
  const range = document.createRange();
  range.setStart(root, 0);
  try {
    range.setEnd(sel.anchorNode!, sel.anchorOffset);
  } catch {
    return 0;
  }
  const frag = range.cloneContents();
  const tmp = document.createElement('div');
  tmp.appendChild(frag);
  return serializeDom(tmp).length;
}

/** Set cursor to `targetOffset` characters into the serialised text. */
function setCaretOffset(root: HTMLElement, targetOffset: number): void {
  let offset = 0;
  for (const node of root.childNodes) {
    if (node.nodeType === Node.TEXT_NODE) {
      const len = (node.textContent ?? '').length;
      if (offset + len >= targetOffset) {
        const sel = window.getSelection()!;
        const range = document.createRange();
        range.setStart(node, targetOffset - offset);
        range.collapse(true);
        sel.removeAllRanges();
        sel.addRange(range);
        return;
      }
      offset += len;
    } else if (node instanceof HTMLElement && node.dataset.mentionPath) {
      const chipLen = `[[FILE:${node.dataset.mentionPath}]]`.length;
      offset += chipLen;
      if (offset >= targetOffset) {
        // Place caret after chip
        const sel = window.getSelection()!;
        const range = document.createRange();
        range.setStartAfter(node);
        range.collapse(true);
        sel.removeAllRanges();
        sel.addRange(range);
        return;
      }
    } else if (node instanceof HTMLBRElement) {
      offset += 1;
    }
  }
  // Fallback: end of content
  const sel = window.getSelection()!;
  const range = document.createRange();
  range.selectNodeContents(root);
  range.collapse(false);
  sel.removeAllRanges();
  sel.addRange(range);
}

/** If caret is immediately after a chip node (collapsed selection), return it. */
function getChipBeforeCaret(root: HTMLElement): HTMLElement | null {
  const sel = window.getSelection();
  if (!sel || !sel.isCollapsed) return null;
  const { anchorNode, anchorOffset } = sel;

  // Caret in a text node at offset 0 → check prev sibling
  if (anchorNode?.nodeType === Node.TEXT_NODE && anchorOffset === 0) {
    const prev = anchorNode.previousSibling;
    if (prev instanceof HTMLElement && prev.dataset.mentionPath) return prev;
  }
  // Caret directly inside root div (anchorNode === root)
  if (anchorNode === root && anchorOffset > 0) {
    const prev = root.childNodes[anchorOffset - 1];
    if (prev instanceof HTMLElement && (prev as HTMLElement).dataset?.mentionPath) {
      return prev as HTMLElement;
    }
  }
  return null;
}

// ── Mention trigger detection ─────────────────────────────────────────────────

function detectMentionTrigger(
  text: string,
  cursor: number,
): { triggerStart: number; query: string } | null {
  if (cursor <= 0) return null;
  let i = cursor - 1;
  while (i >= 0) {
    const ch = text[i];
    if (ch === '@') {
      const before = i === 0 ? '' : text[i - 1];
      if (i === 0 || before === ' ' || before === '\n' || before === '\t') {
        const query = text.slice(i + 1, cursor);
        if (/[\s\n\r]/.test(query)) return null;
        return { triggerStart: i, query };
      }
      return null;
    }
    if (ch === ' ' || ch === '\n' || ch === '\t') return null;
    i--;
  }
  return null;
}

// ── Component API ─────────────────────────────────────────────────────────────

export interface FileTokenInputHandle {
  focus(): void;
  setCaretPosition(pos: number): void;
  getCaretPosition(): number;
  /** Programmatic clear after send — does not fire mention detection. */
  clear(): void;
}

interface MentionTrigger {
  triggerStart: number;
  query: string;
}

export interface FileTokenInputProps {
  value: string;
  onChange(value: string): void;
  onSend(): void;
  onMentionTrigger?(trigger: MentionTrigger | null): void;
  /** Fires when ↑ should move mention picker selection up. */
  onMentionNavUp?(): void;
  /** Fires when ↓ should move mention picker selection down. */
  onMentionNavDown?(): void;
  /** Fires when Enter/Tab confirms the current mention. */
  onMentionConfirm?(): void;
  /** Fires when Escape closes the mention picker. */
  onMentionDismiss?(): void;
  /** When true, ↑↓/Enter/Tab are forwarded to the mention picker, not the DOM. */
  mentionPickerActive?: boolean;
  placeholder?: string;
  disabled?: boolean;
  /** Called with image Files when the user pastes images. */
  onImagePaste?(files: File[]): void;
  className?: string;
}

const FileTokenInput = forwardRef<FileTokenInputHandle, FileTokenInputProps>(
  function FileTokenInput(
    {
      value,
      onChange,
      onSend,
      onMentionTrigger,
      onMentionNavUp,
      onMentionNavDown,
      onMentionConfirm,
      onMentionDismiss,
      mentionPickerActive = false,
      placeholder = '输入消息...',
      disabled = false,
      onImagePaste,
      className,
    },
    ref,
  ) {
    const rootRef = useRef<HTMLDivElement>(null);
    /** The plain-text value that the component currently reflects in its DOM.
     *  We update this whenever we fire onChange so we can detect *external* changes. */
    const internalValueRef = useRef<string>(value);

    // ── Sync external value changes → DOM ──────────────────────────────────
    useEffect(() => {
      if (!rootRef.current) return;
      if (value === internalValueRef.current) return; // our own onChange, skip
      internalValueRef.current = value;
      const root = rootRef.current;
      hydrateFromText(root, value, handleChipDelete);
      // After clearing (empty value), focus so the user can keep typing.
      if (value === '') {
        root.focus();
        const range = document.createRange();
        range.setStart(root, 0);
        range.collapse(true);
        window.getSelection()?.removeAllRanges();
        window.getSelection()?.addRange(range);
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [value]);

    // ── Ref handle ─────────────────────────────────────────────────────────
    useImperativeHandle(ref, () => ({
      focus() {
        rootRef.current?.focus();
      },
      setCaretPosition(pos: number) {
        if (rootRef.current) setCaretOffset(rootRef.current, pos);
      },
      getCaretPosition(): number {
        return rootRef.current ? getCaretOffset(rootRef.current) : 0;
      },
      clear() {
        if (!rootRef.current) return;
        rootRef.current.innerHTML = '';
        internalValueRef.current = '';
        onChange('');
      },
    }));

    // ── Chip delete (from × button or Backspace) ───────────────────────────
    function handleChipDelete(chipNode: HTMLElement) {
      const root = rootRef.current;
      if (!root) return;
      chipNode.remove();
      syncToParent();
      root.focus();
    }

    // ── Sync DOM → parent ──────────────────────────────────────────────────
    function syncToParent() {
      const root = rootRef.current;
      if (!root) return;
      const text = serializeDom(root);
      internalValueRef.current = text;
      onChange(text);
      // Detect mention trigger after sync
      const cursor = getCaretOffset(root);
      onMentionTrigger?.(detectMentionTrigger(text, cursor));
    }

    // ── onInput ────────────────────────────────────────────────────────────
    function handleInput() {
      // Flatten any browser-inserted <div>/<span> wrappers that appear when
      // Enter is pressed in some browsers or when content is pasted as HTML.
      flattenBrowserWrappers();
      syncToParent();
    }

    /** Chrome wraps new lines in <div> elements when the user presses Enter.
     *  We intercept Enter ourselves, but paste of multi-line text can still
     *  produce divs. Convert them to text nodes + <br>. */
    function flattenBrowserWrappers() {
      const root = rootRef.current;
      if (!root) return;
      let dirty = false;
      for (const node of Array.from(root.childNodes)) {
        if (
          node instanceof HTMLElement &&
          node.tagName === 'DIV' &&
          !node.dataset.mentionPath
        ) {
          // Replace div with its children, prepending a <br>
          const br = document.createElement('br');
          root.insertBefore(br, node);
          while (node.firstChild) root.insertBefore(node.firstChild, node);
          node.remove();
          dirty = true;
        }
      }
      if (dirty) {
        // Re-create chip nodes that might have lost their event listeners
        // (unlikely since we only flatten plain divs, but be safe).
        const text = serializeDom(root);
        hydrateFromText(root, text, handleChipDelete);
      }
    }

    // ── onKeyDown ──────────────────────────────────────────────────────────
    function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
      // Mention picker key handling has priority
      if (mentionPickerActive) {
        if (e.key === 'ArrowDown') { e.preventDefault(); onMentionNavDown?.(); return; }
        if (e.key === 'ArrowUp')   { e.preventDefault(); onMentionNavUp?.();   return; }
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault(); onMentionConfirm?.(); return;
        }
        if (e.key === 'Escape') {
          e.preventDefault(); onMentionDismiss?.(); return;
        }
      }

      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        onSend();
        return;
      }

      if (e.key === 'Enter' && e.shiftKey) {
        // Insert <br> manually and reposition caret
        e.preventDefault();
        const sel = window.getSelection();
        if (sel && sel.rangeCount) {
          const range = sel.getRangeAt(0);
          range.deleteContents();
          const br = document.createElement('br');
          range.insertNode(br);
          // Move caret after the <br>
          range.setStartAfter(br);
          range.collapse(true);
          sel.removeAllRanges();
          sel.addRange(range);
          syncToParent();
        }
        return;
      }

      // Backspace chip deletion: if caret is immediately after a chip, remove it.
      if (e.key === 'Backspace' && rootRef.current) {
        const chip = getChipBeforeCaret(rootRef.current);
        if (chip) {
          e.preventDefault();
          handleChipDelete(chip);
          return;
        }
      }
    }

    // ── onPaste ────────────────────────────────────────────────────────────
    function handlePaste(e: RClipboardEvent<HTMLDivElement>) {
      e.preventDefault();
      if (disabled) return;

      const { items } = e.clipboardData;
      const imageFiles = Array.from(items)
        .filter((it) => it.type.startsWith('image/'))
        .map((it) => it.getAsFile())
        .filter(Boolean) as File[];

      if (imageFiles.length > 0 && onImagePaste) {
        onImagePaste(imageFiles);
        return;
      }

      // Plain-text paste — strip HTML, honour [[FILE:]] tokens.
      const plainText =
        e.clipboardData.getData('text/plain') ||
        e.clipboardData.getData('text');
      if (!plainText) return;

      // Insert at cursor via execCommand (deprecated but still widely supported
      // and the simplest way to insert at cursor in contenteditable).
      // Fallback: splice into serialised value.
      try {
        document.execCommand('insertText', false, plainText);
      } catch {
        const root = rootRef.current;
        if (!root) return;
        const cur = getCaretOffset(root);
        const prev = serializeDom(root);
        const next = prev.slice(0, cur) + plainText + prev.slice(cur);
        internalValueRef.current = next;
        hydrateFromText(root, next, handleChipDelete);
        setCaretOffset(root, cur + plainText.length);
      }
      syncToParent();
    }

    // ── onClick — open file / folder when clicking chip body ───────────────
    // (The global [data-jf-file] delegate in fileWorkspaceContext handles this;
    //  no extra onClick needed here. The chip × button uses onMouseDown.)

    // ── Render ─────────────────────────────────────────────────────────────
    return (
      <div
        ref={rootRef}
        contentEditable={disabled ? false : true}
        suppressContentEditableWarning
        className={`${styles.fileTokenInput} ${className ?? ''}`}
        data-placeholder={placeholder}
        onInput={handleInput}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
        // Prevent rich-text drag-and-drop
        onDrop={(e) => e.preventDefault()}
        spellCheck
        aria-multiline="true"
        aria-label={placeholder}
        style={{ opacity: disabled ? 0.5 : undefined, pointerEvents: disabled ? 'none' : undefined }}
      />
    );
  },
);

export default FileTokenInput;
