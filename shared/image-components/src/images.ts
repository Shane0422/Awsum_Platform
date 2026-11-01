import { mount, unmount, flushSync } from 'svelte';
import ImageUploader from './lib/ImageUploader.svelte';
import Avatar from './lib/Avatar.svelte';
import ImageCropper from './lib/ImageCropper.svelte';
import { validateImage } from './lib/imageProcessor';
export { imageConfig } from './lib/imageProcessor';

interface UploaderOptions {
  idPrefix?: string;
  label?: string;
  previewSize?: number;
  fit?: 'cover' | 'contain';
}

export function adjustImage(file: File): Promise<File | null> {
  validateImage(file);
  const target = document.createElement('div');
  document.body.appendChild(target);
  return new Promise(resolve => {
    const finish = async (result: File | null) => {
      await unmount(component);
      target.remove();
      resolve(result);
    };
    const component = mount(ImageCropper, { target, props: {
      file, onapply: (result: File) => { void finish(result); },
      oncancel: () => { void finish(null); },
    } });
  });
}

export function bindImageInput(input: HTMLInputElement, onerror: (message: string) => void,
                               preview?: HTMLImageElement) {
  let processed: File | null = null;
  let busy = false;
  let url: string | null = null;
  let savedPreview: string | null = null;
  const reset = () => {
    processed = null;
    input.value = '';
    if (url) {
      URL.revokeObjectURL(url);
      if (preview && preview.getAttribute('src') === url) {
        if (savedPreview) preview.src = savedPreview;
        else preview.removeAttribute('src');
      }
    }
    url = null;
    savedPreview = null;
  };
  input.closest('.modal')?.addEventListener('hidden.bs.modal', reset);
  input.addEventListener('change', async () => {
    const selected = input.files?.[0];
    if (!selected || busy) return;
    busy = true;
    input.disabled = true;
    input.value = '';
    try {
      const adjusted = await adjustImage(selected);
      if (adjusted) {
        processed = adjusted;
        if (preview) {
          if (!url) savedPreview = preview.getAttribute('src');
          else URL.revokeObjectURL(url);
          url = URL.createObjectURL(adjusted);
          preview.src = url;
        }
        input.dispatchEvent(new CustomEvent<File>('awsumimageapply', { detail: adjusted }));
      }
    } catch (reason) {
      onerror(reason instanceof Error ? reason.message : 'Unable to process image.');
    } finally { busy = false; input.disabled = false; }
  });
  return {
    pending() {
      if (busy) throw new Error('Apply or cancel image adjustment before saving.');
      return processed;
    },
    reset,
  };
}

export function createUploader(target: HTMLElement, options: UploaderOptions = {}) {
  const component = mount(ImageUploader, { target, props: options });
  flushSync();
  return {
    reset(url: string | null = null, saved = false) { flushSync(() => component.reset(url, saved)); },
    setDisabled(value: boolean) { flushSync(() => component.setDisabled(value)); },
    formData(payload: Record<string, unknown>) {
      const { file, removeImage } = component.pending();
      const body = new FormData();
      body.append('payload', JSON.stringify(payload));
      body.append('remove_image', String(removeImage));
      if (file) body.append('image', file);
      return body;
    },
    destroy() { return unmount(component); },
  };
}

export function createAvatar(target: HTMLElement, options: { imageUrl?: string | null; size?: number; name?: string } = {}) {
  const component = mount(Avatar, { target, props: options });
  return {
    setImage(url: string | null) { flushSync(() => component.setImage(url)); },
    destroy() { return unmount(component); },
  };
}

let header: ReturnType<typeof createAvatar> | undefined;
export function updateCurrentAvatar(userId: number | string, url: string | null) {
  const element = document.querySelector<HTMLElement>('[data-current-user-avatar]');
  if (element?.dataset.userId === String(userId)) header?.setImage(url);
}

document.addEventListener('DOMContentLoaded', () => {
  const element = document.querySelector<HTMLElement>('[data-current-user-avatar]');
  if (element) header = createAvatar(element, {
    imageUrl: element.dataset.imageUrl || null, name: element.dataset.name || 'User', size: 26,
  });
});
