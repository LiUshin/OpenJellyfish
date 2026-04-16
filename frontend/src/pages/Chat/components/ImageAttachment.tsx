import { useRef, useCallback, useImperativeHandle, forwardRef } from 'react';
import { message } from 'antd';
import { X } from '@phosphor-icons/react';
import styles from '../chat.module.css';

const MAX_IMAGES = 5;

interface ImageItem {
  dataUrl: string;
  name: string;
}

interface ImageAttachmentProps {
  images: ImageItem[];
  onImagesChange: (images: ImageItem[]) => void;
  disabled?: boolean;
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function processFiles(
  files: File[],
  current: ImageItem[],
): Promise<ImageItem[]> {
  const remaining = MAX_IMAGES - current.length;
  if (remaining <= 0) {
    message.warning(`最多上传 ${MAX_IMAGES} 张图片`);
    return current;
  }

  const imageFiles = files.filter((f) => f.type.startsWith('image/')).slice(0, remaining);
  if (imageFiles.length === 0) return current;

  const newItems: ImageItem[] = await Promise.all(
    imageFiles.map(async (f) => ({
      dataUrl: await readFileAsDataUrl(f),
      name: f.name,
    })),
  );

  if (files.length > remaining) {
    message.warning(`最多上传 ${MAX_IMAGES} 张图片，已忽略多余图片`);
  }

  return [...current, ...newItems];
}

export interface ImageAttachmentHandle {
  triggerUpload: () => void;
}

const ImageAttachment = forwardRef<ImageAttachmentHandle, ImageAttachmentProps>(
  function ImageAttachment({ images, onImagesChange, disabled }, ref) {
    const inputRef = useRef<HTMLInputElement>(null);

    useImperativeHandle(ref, () => ({
      triggerUpload: () => inputRef.current?.click(),
    }));

    const handleFiles = useCallback(
      async (files: File[]) => {
        const next = await processFiles(files, images);
        if (next !== images) onImagesChange(next);
      },
      [images, onImagesChange],
    );

    const handleDrop = useCallback(
      (e: React.DragEvent) => {
        if (disabled) return;
        e.preventDefault();
        e.stopPropagation();
        const files = Array.from(e.dataTransfer.files).filter((f) =>
          f.type.startsWith('image/'),
        );
        if (files.length > 0) handleFiles(files);
      },
      [disabled, handleFiles],
    );

    const handleDragOver = useCallback((e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
    }, []);

    const handleInputChange = useCallback(
      (e: React.ChangeEvent<HTMLInputElement>) => {
        const files = Array.from(e.target.files ?? []);
        handleFiles(files);
        if (inputRef.current) inputRef.current.value = '';
      },
      [handleFiles],
    );

    const removeImage = useCallback(
      (idx: number) => {
        onImagesChange(images.filter((_, i) => i !== idx));
      },
      [images, onImagesChange],
    );

    if (images.length === 0) {
      return (
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          multiple
          style={{ display: 'none' }}
          onChange={handleInputChange}
        />
      );
    }

    return (
      <div
        className={styles.imageAttachmentRoot}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
      >
        <div className={styles.imageThumbnailBar}>
          {images.map((img, i) => (
            <div key={i} className={styles.imageThumbnailWrap}>
              <img src={img.dataUrl} alt={img.name} className={styles.imageThumbnail} />
              <button
                className={styles.imageThumbnailRemove}
                onClick={() => removeImage(i)}
                disabled={disabled}
                type="button"
              >
                <X size={10} weight="bold" />
              </button>
            </div>
          ))}
        </div>

        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          multiple
          style={{ display: 'none' }}
          onChange={handleInputChange}
        />
      </div>
    );
  },
);

export default ImageAttachment;
