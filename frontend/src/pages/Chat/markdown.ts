import { marked } from 'marked';
import hljs from 'highlight.js/lib/core';
import DOMPurify from 'dompurify';
import { mediaUrl } from '../../services/api';

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

function filePathToHtml(filePath: string): string | null {
  const type = getMediaType(filePath);
  if (!type) return null;
  const url = mediaUrl(filePath);
  const name = escapeHtml(filePath.split('/').pop() || filePath);

  switch (type) {
    case 'image':
      return `<div class="jf-media jf-media-image">
        <img src="${url}" alt="${name}" loading="lazy"
             onclick="window.open(this.src,'_blank')" title="点击查看大图" />
        <div class="jf-media-caption">${name}</div>
      </div>`;
    case 'audio':
      return `<div class="jf-media jf-media-audio">
        <div class="jf-media-caption">🎵 ${name}</div>
        <audio controls preload="metadata" src="${url}">浏览器不支持音频播放</audio>
      </div>`;
    case 'video':
      return `<div class="jf-media jf-media-video">
        <video controls preload="metadata" src="${url}">浏览器不支持视频播放</video>
        <div class="jf-media-caption">${name}</div>
      </div>`;
    case 'pdf':
      return `<div class="jf-media jf-media-pdf">
        <div class="jf-media-caption">📄 ${name}
          <a href="${url}" target="_blank" rel="noopener" style="margin-left:8px;color:var(--jf-accent)">新窗口打开</a>
        </div>
        <iframe src="${url}" style="width:100%;height:400px;border:none;border-radius:8px"></iframe>
      </div>`;
    case 'html': {
      const hid = 'html-' + Math.random().toString(36).slice(2, 10);
      return `<div class="jf-media jf-media-html" id="${hid}">
        <div class="jf-media-caption">📊 ${name}
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

const FILE_TAG_RE = /<?<FILE:(\/[^>]+?)>>?/gi;

function preProcessMediaTags(text: string): string {
  return text.replace(FILE_TAG_RE, (_match, filePath: string) => {
    const html = filePathToHtml(filePath.trim());
    return html ? `\n\n${html}\n\n` : _match;
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

export function renderMarkdown(text: string): string {
  try {
    const preprocessed = preProcessMediaTags(text);
    const html = String(marked.parse(preprocessed));
    const postProcessed = postProcessMediaSrc(html);
    return DOMPurify.sanitize(postProcessed, SANITIZE_OPTS) as string;
  } catch {
    return DOMPurify.sanitize(text.replace(/\n/g, '<br>')) as string;
  }
}

export function escapeHtml(str: string): string {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}
