/**
 * 上次选中的 LLM model id（admin /chat 与 service-chat 共用）。
 *
 * 行为：
 *   - 用户在 chat 页下拉框选某个模型 → 写入 localStorage
 *   - 页面刷新 / 重开后 loadModels 优先用本地值；本地值若已不在
 *     可用列表（catalog 删了 / 凭据失效），回退到后端给的 default
 *
 * 仅做本地记忆，不影响后端 capability_defaults（设置页那个仍然是真·全局默认）。
 */
export const LAST_MODEL_KEY = 'last_selected_llm';

export function getLastSelectedModel(): string {
  try {
    return localStorage.getItem(LAST_MODEL_KEY) || '';
  } catch {
    return '';
  }
}

export function setLastSelectedModel(modelId: string): void {
  try {
    if (modelId) localStorage.setItem(LAST_MODEL_KEY, modelId);
  } catch {
    /* ignore */
  }
}
