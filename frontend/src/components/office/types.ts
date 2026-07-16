/** 统一字节源：admin 走 downloadFile，service-chat 走带 token 的 media URL。 */
export type OfficeBufferSource = () => Promise<ArrayBuffer>;

export interface OfficePreviewProps {
  getArrayBuffer: OfficeBufferSource;
  /** 用于错误提示 / 下载文件名 */
  fileName?: string;
}
