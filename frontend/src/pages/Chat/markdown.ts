import { marked } from 'marked';
import hljs from 'highlight.js/lib/core';
import DOMPurify from 'dompurify';
import { mediaUrl as adminMediaUrl } from '../../services/api';

// ── Media URL builder (可注入) ────────────────────────────────────────
// admin /chat 默认走 adminMediaUrl（带 admin Bearer token）；
// service-chat 入口在启动时调用 setMediaUrlBuilder() 注入 consumer 端的 builder
// （带 service API key + conversation 范围），实现一份 markdown 渲染逻辑两边复用。
type MediaUrlBuilder = (path: string) => string;
let _mediaUrlBuilder: MediaUrlBuilder = adminMediaUrl;

export function setMediaUrlBuilder(fn: MediaUrlBuilder): void {
  _mediaUrlBuilder = fn;
}
function mediaUrl(path: string): string {
  return _mediaUrlBuilder(path);
}

// ── 文件「在文件浏览器中打开」开关 ────────────────────────────────────
// admin 端默认开启：把非媒体的 <<FILE:>> 渲染成可点击的小 pill，
// 同时给媒体 caption 加一个「📁 在文件浏览器打开」小按钮。
// 服务化（service-chat / consumer）端没有 FilePanel，可调用
// setFileRevealEnabled(false) 关闭，避免渲染出无效的可点击元素。
let _fileRevealEnabled = true;
export function setFileRevealEnabled(enabled: boolean): void {
  _fileRevealEnabled = enabled;
}

import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import python from 'highlight.js/lib/languages/python';
import bash from 'highlight.js/lib/languages/bash';
import json from 'highlight.js/lib/languages/json';
import sql from 'highlight.js/lib/languages/sql';
import xml from 'highlight.js/lib/languages/xml';
import css from 'highlight.js/lib/languages/css';
import markdown from 'highlight.js/lib/languages/markdown';
import yaml from 'highlight.js/lib/languages/yaml';
import java from 'highlight.js/lib/languages/java';
import go from 'highlight.js/lib/languages/go';
import rust from 'highlight.js/lib/languages/rust';
import cpp from 'highlight.js/lib/languages/cpp';
import diff from 'highlight.js/lib/languages/diff';
import dockerfile from 'highlight.js/lib/languages/dockerfile';
import ini from 'highlight.js/lib/languages/ini';

hljs.registerLanguage('javascript', javascript);
hljs.registerLanguage('js', javascript);
hljs.registerLanguage('typescript', typescript);
hljs.registerLanguage('ts', typescript);
hljs.registerLanguage('python', python);
hljs.registerLanguage('py', python);
hljs.registerLanguage('bash', bash);
hljs.registerLanguage('sh', bash);
hljs.registerLanguage('shell', bash);
hljs.registerLanguage('json', json);
hljs.registerLanguage('sql', sql);
hljs.registerLanguage('html', xml);
hljs.registerLanguage('xml', xml);
hljs.registerLanguage('css', css);
hljs.registerLanguage('markdown', markdown);
hljs.registerLanguage('md', markdown);
hljs.registerLanguage('yaml', yaml);
hljs.registerLanguage('yml', yaml);
hljs.registerLanguage('java', java);
hljs.registerLanguage('go', go);
hljs.registerLanguage('rust', rust);
hljs.registerLanguage('cpp', cpp);
hljs.registerLanguage('c', cpp);
hljs.registerLanguage('diff', diff);
hljs.registerLanguage('dockerfile', dockerfile);
hljs.registerLanguage('ini', ini);
hljs.registerLanguage('toml', ini);

function highlightCode(this: unknown, { text, lang }: { text: string; lang?: string }): string {
  let out: string;
  if (lang && hljs.getLanguage(lang)) {
    out = hljs.highlight(text, { language: lang }).value;
  } else {
    out = hljs.highlightAuto(text).value;
  }
  const cls = lang ? 'hljs language-' + lang : 'hljs';
  return '<pre><code class="' + cls + '">' + out + '</code></pre>';
}

marked.use({
  breaks: true,
  gfm: true,
  renderer: { code: highlightCode },
});

// ── Media file embedding (<<FILE:path>> tags) ──────────────────────

const MEDIA_EXTS: Record<string, string[]> = {
  image: ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.ico'],
  audio: ['.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac', '.wma'],
  video: ['.mp4', '.webm', '.ogv', '.mov', '.avi', '.mkv'],
  pdf:   ['.pdf'],
  html:  ['.html', '.htm'],
};

const ALL_MEDIA_EXT_RE = Object.values(MEDIA_EXTS).flat().map(e => e.slice(1)).join('|');

function getMediaType(filePath: string): string | null {
  const ext = filePath.slice(filePath.lastIndexOf('.')).toLowerCase();
  for (const [type, exts] of Object.entries(MEDIA_EXTS)) {
    if (exts.includes(ext)) return type;
  }
  return null;
}

/** 生成 caption 里「📁 在文件浏览器打开」的小按钮 HTML（可作为 a 标签文本添加，
 *  父元素 .jf-media-caption 已是 flex；此处用 margin-left:auto 把它推到右边）。
 *  仅在 _fileRevealEnabled = true 时返回内容；否则返回空串。
 *  data-jf-file 由 fileWorkspaceContext 的全局点击委托捕获并触发 revealInBrowser。 */
function buildRevealAction(filePath: string): string {
  if (!_fileRevealEnabled) return '';
  const safe = escapeHtml(filePath);
  return ` <button type="button" class="jf-file-reveal" data-jf-file="${safe}" title="在文件浏览器中定位">📁</button>`;
}

function filePathToHtml(filePath: string): string | null {
  const type = getMediaType(filePath);
  if (!type) return null;
  const url = mediaUrl(filePath);
  const name = escapeHtml(filePath.split('/').pop() || filePath);
  const reveal = buildRevealAction(filePath);

  switch (type) {
    case 'image':
      return `<div class="jf-media jf-media-image">
        <img src="${url}" alt="${name}" loading="lazy"
             onclick="window.open(this.src,'_blank')" title="点击查看大图" />
        <div class="jf-media-caption">${name}${reveal}</div>
      </div>`;
    case 'audio':
      return `<div class="jf-media jf-media-audio">
        <div class="jf-media-caption">🎵 ${name}${reveal}</div>
        <audio controls preload="metadata" src="${url}">浏览器不支持音频播放</audio>
      </div>`;
    case 'video':
      return `<div class="jf-media jf-media-video">
        <video controls preload="metadata" src="${url}">浏览器不支持视频播放</video>
        <div class="jf-media-caption">${name}${reveal}</div>
      </div>`;
    case 'pdf':
      return `<div class="jf-media jf-media-pdf">
        <div class="jf-media-caption">📄 ${name}${reveal}
          <a href="${url}" target="_blank" rel="noopener" style="margin-left:8px;color:var(--jf-accent)">新窗口打开</a>
        </div>
        <iframe src="${url}" style="width:100%;height:400px;border:none;border-radius:8px"></iframe>
      </div>`;
    case 'html': {
      const hid = 'html-' + Math.random().toString(36).slice(2, 10);
      return `<div class="jf-media jf-media-html" id="${hid}">
        <div class="jf-media-caption">📊 ${name}${reveal}
          <span style="margin-left:auto;display:flex;gap:4px">
            <button onclick="document.getElementById('${hid}').classList.toggle('jf-media-expanded')"
                    style="background:none;border:none;color:var(--jf-text-muted);cursor:pointer;font-size:14px" title="展开/收起">⛶</button>
            <a href="${url}" target="_blank" rel="noopener"
               style="color:var(--jf-text-muted);font-size:14px;text-decoration:none" title="新窗口打开">↗</a>
          </span>
        </div>
        <iframe src="${url}" sandbox="allow-scripts allow-same-origin allow-popups"
                loading="lazy" style="width:100%;height:360px;border:none;border-radius:8px"></iframe>
      </div>`;
    }
    default:
      return null;
  }
}

/** 非媒体文件（.txt / .json / .py / 任意未知扩展）的 <<FILE:>> 渲染：
 *  改为可点击的内联 pill。点击后由 fileWorkspaceContext 的全局委托
 *  捕获 [data-jf-file] 并调用 revealInBrowser → 同时打开文件 + 跳转浏览器目录。 */
function nonMediaFileToHtml(filePath: string): string {
  const safePath = escapeHtml(filePath);
  const name = escapeHtml(filePath.split('/').pop() || filePath);
  if (!_fileRevealEnabled) {
    return `&lt;FILE:${safePath}&gt;`;
  }
  return `<button type="button" class="jf-file-link" data-jf-file="${safePath}" title="${safePath} · 点击在文件浏览器中打开"><span class="jf-file-link-icon">📄</span><span class="jf-file-link-name">${name}</span></button>`;
}

const FILE_TAG_RE = /<?<FILE:(\/[^>]+?)>>?/gi;

function preProcessMediaTags(text: string): string {
  return text.replace(FILE_TAG_RE, (_match, filePath: string) => {
    const trimmed = filePath.trim();
    const html = filePathToHtml(trimmed);
    if (html) return `\n\n${html}\n\n`;
    // 非媒体文件：渲染成内联可点击 pill（不换行，便于嵌在句子中）。
    return nonMediaFileToHtml(trimmed);
  });
}

function postProcessMediaSrc(html: string): string {
  const extPat = `\\.(?:${ALL_MEDIA_EXT_RE})`;
  const imgRe = new RegExp(`<img\\s+src="(\\/[^"]+${extPat})"([^>]*)>`, 'gi');
  return html.replace(imgRe, (_m, path: string, rest: string) => {
    if (path.includes('/api/files/media')) return _m;
    const decoded = decodeURIComponent(path);
    const url = mediaUrl(decoded);
    const name = escapeHtml(decoded.split('/').pop() || decoded);
    return `<div class="jf-media jf-media-image">
      <img src="${url}" ${rest} loading="lazy"
           onclick="window.open(this.src,'_blank')" title="点击查看大图" />
      <div class="jf-media-caption">${name}</div>
    </div>`;
  });
}

// ── Markdown render pipeline ────────────────────────────────────────

const SANITIZE_OPTS = {
  ADD_TAGS: ['iframe', 'audio', 'video', 'source'],
  ADD_ATTR: [
    'target', 'sandbox', 'loading', 'frameborder', 'allowfullscreen',
    'controls', 'preload', 'src', 'autoplay', 'onclick', 'title',
  ],
  ADD_DATA_URI_TAGS: ['img'],
};

// ── LRU 缓存：避免历史消息每次父组件 re-render 都重跑 marked+hljs+DOMPurify ──
// 长文本 key 会进 cache，但 256 个上限足够覆盖一屏对话的历史块。
// 流式文本每次都是新字符串 → cache miss + 旧条目自然被挤出，无副作用。
// 我们 cache 的 key 同时包含 _fileRevealEnabled，因为 admin / consumer 渲染结果不同。
const MD_CACHE_LIMIT = 256;
const _mdCache = new Map<string, string>();

function _mdCacheGet(key: string): string | undefined {
  const hit = _mdCache.get(key);
  if (hit !== undefined) {
    // LRU: 命中后移到队尾。
    _mdCache.delete(key);
    _mdCache.set(key, hit);
  }
  return hit;
}

function _mdCacheSet(key: string, value: string): void {
  if (_mdCache.size >= MD_CACHE_LIMIT) {
    const oldest = _mdCache.keys().next().value;
    if (oldest !== undefined) _mdCache.delete(oldest);
  }
  _mdCache.set(key, value);
}

export function renderMarkdown(text: string): string {
  // 极短或空文本不缓存（避免 cache 噪音 + key 冲突收益小）。
  const cacheable = text.length > 16 && text.length < 200_000;
  const key = cacheable ? `${_fileRevealEnabled ? '1' : '0'}|${text}` : '';
  if (cacheable) {
    const hit = _mdCacheGet(key);
    if (hit !== undefined) return hit;
  }
  let result: string;
  try {
    const preprocessed = preProcessMediaTags(text);
    const html = String(marked.parse(preprocessed));
    const postProcessed = postProcessMediaSrc(html);
    result = DOMPurify.sanitize(postProcessed, SANITIZE_OPTS) as string;
  } catch {
    result = DOMPurify.sanitize(text.replace(/\n/g, '<br>')) as string;
  }
  if (cacheable) _mdCacheSet(key, result);
  return result;
}

export function escapeHtml(str: string): string {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}
