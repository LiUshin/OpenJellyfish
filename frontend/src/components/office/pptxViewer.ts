let viewerModule: Promise<typeof import('pptxviewjs')> | undefined;

/** PptxViewJS installs its helper as window.FileReader on import. Keep the
 * browser reader intact for uploads, voice input and other Office previews. */
export function loadPptxViewer() {
  if (!viewerModule) {
    const browserFileReader = window.FileReader;
    viewerModule = import('pptxviewjs')
      .finally(() => { window.FileReader = browserFileReader; })
      .catch(error => { viewerModule = undefined; throw error; });
  }
  return viewerModule;
}
