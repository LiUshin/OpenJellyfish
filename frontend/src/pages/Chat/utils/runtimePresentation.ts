import type { RuntimeArtifact } from '../../../services/runtime';
import type { StreamBlock } from '../types';

/** Resolve only exact names of this run's archived files. Never turn a host path
 * into an admin storage path by stripping arbitrary workspace prefixes. */
export function runtimePresentation(blocks: StreamBlock[], artifacts: RuntimeArtifact[]) {
  if (!artifacts.length) return { blocks };
  const aliases = new Map<string, string>();
  const key = (path: string) => path.replace(/^\.\//, '').replace(/^\//, '');
  artifacts.forEach(file => { aliases.set(key(file.name), file.path); aliases.set(key(file.path), file.path); });
  const associated = new Set<string>();
  const projected = blocks.map(block => {
    if (block.type !== 'tool' || !block.changes?.length) return block;
    return { ...block, changes: block.changes.map(change => {
      const target = aliases.get(key(change.workspace_path ?? change.path));
      if (target) { aliases.set(key(change.path), target); associated.add(target); }
      return { ...change, preview_path: target };
    }) };
  });
  const link = (path: string, fallback: string) => {
    let decoded = path;
    try { decoded = decodeURIComponent(path); } catch { /* leave malformed URI untouched */ }
    const [bare, ...fragment] = decoded.split('#');
    const target = aliases.get(key(bare));
    if (!target) return fallback;
    associated.add(target);
    return `<<FILE:${target}${fragment.length ? '#' + fragment.join('#') : ''}>>`;
  };
  const presented: StreamBlock[] = projected.map(block => {
    if (block.type !== 'text') return block;
    // Rewrite references where they already occur. Archiving must never extract
    // links from the answer or add a second, differently styled file list.
    const content = block.content.split(/(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$))/g)
      .map((part, i) => i % 2 ? part : part
        .replace(/<?<FILE:([^>]+)>>?/gi, (match, path: string) => link(path, match))
        .replace(/!?\[[^\]\n]+\]\(([^)\n]+)\)/g, (match, path: string) => link(path, match))
        .replace(/`([^`\n]+)`/g, (match, path: string) => link(path, match))).join('');
    return content === block.content ? block : { ...block, content };
  });
  const missing = artifacts.filter(file => !associated.has(file.path));
  if (missing.length) {
    // Use the existing FILE renderer at the tail, including its media previews
    // and workspace links. A separate block also keeps unfinished code fences
    // in the answer from swallowing these fallback entries.
    presented.push({ type: 'text', content: missing.map(file => `<<FILE:${file.path}>>`).join('\n\n') });
  }
  return { blocks: presented };
}
