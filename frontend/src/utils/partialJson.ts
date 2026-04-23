/**
 * Partial / streaming JSON helpers.
 *
 * 适用场景：LLM 流式生成工具调用参数时，前端需要在 JSON 还没收完整之前，
 * 提取某个字段（如 write_file 的 content）的「已到达部分」并实时渲染。
 *
 * 设计取舍：
 * - 不引第三方依赖（partialjson / parse-partial-json）。我们的 JSON 结构很简单
 *   （顶层一个对象 + 字符串/数字字段），手写一个状态机足够。
 * - 严格只处理 `"<field>"` 的双引号 key。LLM 输出的 tool_call args 都是 JSON 标准。
 * - 正确处理转义：\" \\\\ \\n \\t \\u00xx 等。
 */

/**
 * 在增量 JSON 字符串中，查找 `"<field>"` 后跟随的字符串值，返回已到达部分。
 *
 * 例：
 *   extractStreamingField('{"file_path":"a.py","content":"import os\\n', 'content')
 *     → 'import os\n'
 *   extractStreamingField('{"file_path":"a.py","content":"hi"}', 'content')
 *     → 'hi'
 *
 * 找不到字段或字段值是非字符串（数字/对象/null），返回 null。
 *
 * @param raw  原始（可能不完整）JSON 字符串
 * @param field 字段名（不带引号）
 */
export function extractStreamingField(raw: string, field: string): string | null {
  if (!raw) return null;

  // 用「字符串字面量感知」的查找：避免 `"content"` 出现在另一个字段的 value 里造成误判。
  // 状态机：扫描整个 raw，跟踪「当前是否在字符串字面量中」与「上一次落在 value 位置的 key 名」。

  let i = 0;
  const n = raw.length;
  let inString = false;
  let escape = false;
  let stringStart = -1;
  let lastKey: string | null = null;
  let waitingForValue = false;

  while (i < n) {
    const ch = raw[i];

    if (escape) {
      escape = false;
      i++;
      continue;
    }

    if (inString) {
      if (ch === '\\') {
        escape = true;
        i++;
        continue;
      }
      if (ch === '"') {
        // 字符串闭合
        const literal = raw.slice(stringStart + 1, i);
        inString = false;

        if (waitingForValue && lastKey === field) {
          // 完整字段值（已闭合）→ 解析转义
          return decodeJsonString(literal);
        }

        // 判断这个字符串是 key 还是 value：
        // 在合法 JSON 对象里，`"key": "value"` 中 key 后必有 `:`，value 后必有 `,` 或 `}`。
        // 简化策略：跳过空白后看下一个字符。
        let j = i + 1;
        while (j < n && /\s/.test(raw[j])) j++;
        if (j < n && raw[j] === ':') {
          lastKey = literal;
          waitingForValue = true;
        } else {
          // 是 value（或数组元素），结束等待
          waitingForValue = false;
          lastKey = null;
        }

        i++;
        continue;
      }
      i++;
      continue;
    }

    // not in string
    if (ch === '"') {
      inString = true;
      stringStart = i;
      i++;
      continue;
    }

    // 进入 value 后遇到非字符串（数字 / true / false / null / { / [）→ 当前 key 不是我们要的字符串字段
    if (waitingForValue && ch !== ':' && !/\s/.test(ch)) {
      if (lastKey === field) {
        // 字段存在但 value 类型不是字符串
        return null;
      }
      // 否则跳过这个 value（粗略：吃到下一个 `,` 或 `}`，且要避开嵌套字符串）
      // 简化版：交给主循环慢慢推进，遇到下一个 key 时 waitingForValue 会被重新设置
      waitingForValue = false;
      lastKey = null;
    }

    i++;
  }

  // 走到末尾：要么 lastKey === field 且仍在 inString（未闭合）→ 返回已到达部分
  if (inString && waitingForValue && lastKey === field && stringStart >= 0) {
    const partial = raw.slice(stringStart + 1);
    // partial 是「未闭合」字符串字面量，可能以 `\` 结尾（半个转义）。
    // 容错：把可能的尾部反斜杠剥掉再 decode。
    const safe = partial.endsWith('\\') && !partial.endsWith('\\\\')
      ? partial.slice(0, -1)
      : partial;
    return decodeJsonString(safe);
  }

  return null;
}

/**
 * 把 JSON 字符串字面量（不含外层引号）的转义序列解码成真实字符。
 * 容错：遇到非法转义保留原样，不抛异常。
 */
export function decodeJsonString(literal: string): string {
  let out = '';
  let i = 0;
  while (i < literal.length) {
    const ch = literal[i];
    if (ch !== '\\') {
      out += ch;
      i++;
      continue;
    }
    const next = literal[i + 1];
    if (next === undefined) {
      // 末尾孤立反斜杠（流式不完整），丢弃
      break;
    }
    switch (next) {
      case '"': out += '"'; i += 2; break;
      case '\\': out += '\\'; i += 2; break;
      case '/': out += '/'; i += 2; break;
      case 'n': out += '\n'; i += 2; break;
      case 't': out += '\t'; i += 2; break;
      case 'r': out += '\r'; i += 2; break;
      case 'b': out += '\b'; i += 2; break;
      case 'f': out += '\f'; i += 2; break;
      case 'u': {
        const hex = literal.slice(i + 2, i + 6);
        if (/^[0-9a-fA-F]{4}$/.test(hex)) {
          out += String.fromCharCode(parseInt(hex, 16));
          i += 6;
        } else {
          // 流式中只到达半个 \uXXXX，保留原样跳过
          out += '\\u';
          i += 2;
        }
        break;
      }
      default:
        out += next;
        i += 2;
    }
  }
  return out;
}
