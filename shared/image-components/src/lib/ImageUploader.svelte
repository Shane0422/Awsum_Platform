<script lang="ts">
  import { onDestroy } from 'svelte';
  import ImageCropper from './ImageCropper.svelte';
  import { imageConfig, validateImage } from './imageProcessor';

  interface Props {
    idPrefix?: string;
    label?: string;
    previewSize?: number;
    fit?: 'cover' | 'contain';
  }
  let {
    idPrefix = 'awsumImage', label = 'Image', previewSize = 120, fit = 'contain',
  }: Props = $props();
  let currentUrl = $state<string | null>(null);
  let hasSavedImage = $state(false);
  let file = $state<File | null>(null);
  let removed = $state(false);
  let objectUrl = $state<string | null>(null);
  let error = $state('');
  let invalid = $state(false);
  let decoding = $state(false);
  let disabled = $state(false);
  let failedPreview = $state(false);
  let cropFile = $state<File | null>(null);
  let input: HTMLInputElement;
  const source = $derived(objectUrl || (!removed ? currentUrl : null));

  function release() {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
  }

  export function reset(url: string | null = null, saved: boolean = false) {
    release();
    cropFile = null;
    currentUrl = url;
    hasSavedImage = saved;
    file = null;
    removed = false;
    invalid = false;
    decoding = false;
    failedPreview = false;
    disabled = false;
    error = !url && saved ? 'The saved image is unavailable. You can replace or remove its reference.' : '';
    if (input) input.value = '';
  }

  export function setDisabled(value: boolean) { disabled = value; }

  export function pending() {
    if (cropFile) throw new Error('Apply or cancel image adjustment before saving.');
    if (invalid || decoding) throw new Error(error || 'Please wait for the image preview to load.');
    return { file, removeImage: removed };
  }

  function choose(event: Event) {
    const selected = (event.currentTarget as HTMLInputElement).files?.[0];
    if (!selected) return;
    error = '';
    try { validateImage(selected); }
    catch (reason) { error = reason instanceof Error ? reason.message : 'Unable to validate image.'; }
    invalid = !!error;
    if (invalid) {
      input.value = '';
      return;
    }
    input.value = '';
    cropFile = selected;
  }

  function accept(selected: File) {
    release();
    file = selected;
    removed = false;
    failedPreview = false;
    decoding = true;
    objectUrl = URL.createObjectURL(selected);
  }

  function applyCrop(selected: File) {
    cropFile = null;
    accept(selected);
  }

  function remove() {
    release();
    file = null;
    removed = hasSavedImage;
    invalid = false;
    decoding = false;
    failedPreview = false;
    error = '';
    input.value = '';
  }

  function previewFailed() {
    failedPreview = true;
    decoding = false;
    invalid = !!file;
    error = file ? 'This image cannot be previewed. Choose another image.' : 'The saved image is unavailable.';
  }
  onDestroy(release);
</script>

<div class="awsum-image-uploader" style:--preview-size={`${previewSize}px`}>
  <span class="awsum-image-label">{label}</span>
  <div class="awsum-image-preview">
    {#if !source || failedPreview}
      <span class="awsum-image-placeholder">
        <img src={fit === 'cover' ? '/images/platform/defaults/default-user.svg' : '/images/platform/defaults/no-image.svg'} alt="" />
        {fit === 'cover' ? 'No photo' : 'No image'}
      </span>
    {/if}
    {#if source && !failedPreview}
      {#key source}
        <img id={`${idPrefix}Preview`} src={source} alt={`${label} preview`} style:object-fit={fit}
          onload={() => { decoding = false; }} onerror={previewFailed} />
      {/key}
    {/if}
  </div>
  <input bind:this={input} id={`${idPrefix}File`} type="file" accept=".jpg,.jpeg,.png,.webp"
    aria-label={`Select ${label}`} disabled={disabled} onchange={choose} class="awsum-image-input" />
  <div class="awsum-image-actions">
    <button id={`${idPrefix}Choose`} type="button" disabled={disabled} onclick={() => input.click()}>
      {source ? 'Replace' : 'Upload'}
    </button>
    <button id={`${idPrefix}Remove`} type="button" class="awsum-image-remove"
      disabled={disabled || (!source && !hasSavedImage && !invalid)} onclick={remove}>Remove</button>
  </div>
  <small>JPG / PNG / WEBP - {imageConfig.maxInputBytes / 1024 / 1024} MB</small>
  <div id={`${idPrefix}Error`} class="awsum-image-error" role="alert">{error}</div>
</div>

{#if cropFile}
  <ImageCropper file={cropFile} onapply={applyCrop} oncancel={() => { cropFile = null; }} />
{/if}

<style>
  .awsum-image-uploader { width: max-content; max-width: 100%; color: #64748b; font-size: 11px; }
  .awsum-image-label { display: block; font-size: 12px; font-weight: 600; color: #3b4a5e; margin-bottom: 5px; }
  .awsum-image-preview { box-sizing: border-box; width: var(--preview-size); height: var(--preview-size); border: 1px solid #dfe6ee; border-radius: 8px; background: #f7f9fc; display: flex; align-items: center; justify-content: center; overflow: hidden; }
  .awsum-image-preview img { width: 100%; height: 100%; }
  .awsum-image-placeholder { display: grid; justify-items: center; gap: 4px; }
  .awsum-image-placeholder img { width: 42px; height: 42px; }
  .awsum-image-input { position: absolute; width: 1px; height: 1px; padding: 0; clip-path: inset(50%); overflow: hidden; }
  .awsum-image-actions { display: flex; gap: 5px; margin: 6px 0 4px; }
  .awsum-image-actions button { min-height: 28px !important; border: 1px solid #cbd5e1; border-radius: 5px; padding: 3px 8px; color: #2459a8; background: white; font: inherit; cursor: pointer; }
  .awsum-image-actions .awsum-image-remove { color: #b42332; }
  .awsum-image-actions button:disabled { opacity: .5; cursor: default; }
  .awsum-image-error { max-width: 180px; color: #b42332; margin-top: 3px; white-space: normal; }
  small { font-size: 10px; white-space: nowrap; }
</style>
