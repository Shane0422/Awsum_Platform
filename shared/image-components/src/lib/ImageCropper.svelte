<script lang="ts">
  import { onMount } from 'svelte';
  import { imageConfig, processImage } from './imageProcessor';

  interface Props {
    file: File;
    onapply: (file: File) => void;
    oncancel: () => void;
  }
  let { file, onapply, oncancel }: Props = $props();
  let dialog: HTMLDialogElement;
  let canvas: HTMLCanvasElement;
  let image: HTMLImageElement | null = null;
  let ready = $state(false);
  let processing = $state(false);
  let error = $state('');
  let zoom = $state(1);
  let x = 0;
  let y = 0;
  let pointer: { id: number; x: number; y: number } | null = null;
  const frame = 300;
  let baseScale = 1;
  let maxZoom = $state(imageConfig.maxCoverZoom);

  function draw() {
    if (!image) return;
    const scale = baseScale * zoom;
    const width = image.naturalWidth * scale;
    const height = image.naturalHeight * scale;
    // Keep at least a quarter of the smaller image/frame dimension visible on each axis.
    const limitX = (frame + width) / 2 - Math.min(frame, width) / 4;
    const limitY = (frame + height) / 2 - Math.min(frame, height) / 4;
    x = Math.max(-limitX, Math.min(limitX, x));
    y = Math.max(-limitY, Math.min(limitY, y));
    const context = canvas.getContext('2d');
    if (!context) { error = 'Image adjustment is not supported in this browser.'; return; }
    context.fillStyle = imageConfig.background;
    context.fillRect(0, 0, frame, frame);
    context.drawImage(image, (frame - width) / 2 + x, (frame - height) / 2 + y, width, height);
  }

  function reset() { zoom = 1; x = 0; y = 0; draw(); }

  function start(event: PointerEvent) {
    if (!ready || processing || pointer || event.button !== 0) return;
    canvas.setPointerCapture(event.pointerId);
    pointer = { id: event.pointerId, x: event.clientX, y: event.clientY };
  }

  function move(event: PointerEvent) {
    if (!pointer || pointer.id !== event.pointerId) return;
    const ratio = frame / canvas.getBoundingClientRect().width;
    x += (event.clientX - pointer.x) * ratio;
    y += (event.clientY - pointer.y) * ratio;
    pointer = { id: pointer.id, x: event.clientX, y: event.clientY };
    draw();
  }

  function stop(event: PointerEvent) {
    if (pointer?.id === event.pointerId) pointer = null;
  }

  function keydown(event: KeyboardEvent) {
    const offsets: Record<string, [number, number]> = {
      ArrowLeft: [-10, 0], ArrowRight: [10, 0], ArrowUp: [0, -10], ArrowDown: [0, 10],
    };
    const offset = offsets[event.key];
    if (!offset || !ready || processing) return;
    event.preventDefault();
    x += offset[0]; y += offset[1]; draw();
  }

  async function apply() {
    if (!ready || !image || processing) return;
    processing = true;
    error = '';
    try {
      const scale = baseScale * zoom;
      const sx = (image.naturalWidth * scale - frame) / 2 - x;
      const sy = (image.naturalHeight * scale - frame) / 2 - y;
      onapply(await processImage(image, { x: sx / scale, y: sy / scale, size: frame / scale }));
    } catch (reason) {
      error = reason instanceof Error ? reason.message : 'Image processing failed. Please try again.';
    } finally { processing = false; }
  }

  onMount(() => {
    const previousFocus = document.activeElement;
    const url = URL.createObjectURL(file);
    const photo = new Image();
    let active = true;
    dialog.showModal();
    photo.onload = () => {
      if (!active) return;
      if (photo.naturalWidth * photo.naturalHeight > imageConfig.maxInputPixels) {
        error = 'Image dimensions must not exceed 20 megapixels.';
        URL.revokeObjectURL(url);
        return;
      }
      image = photo;
      baseScale = Math.min(frame / photo.naturalWidth, frame / photo.naturalHeight);
      const coverScale = Math.max(frame / photo.naturalWidth, frame / photo.naturalHeight);
      maxZoom = imageConfig.maxCoverZoom * coverScale / baseScale;
      ready = true;
      reset();
      URL.revokeObjectURL(url);
    };
    photo.onerror = () => { if (active) error = 'This image cannot be decoded. Choose a valid JPG, PNG or WEBP image.'; };
    photo.src = url;
    return () => {
      active = false;
      photo.onload = null; photo.onerror = null;
      photo.src = '';
      image = null;
      URL.revokeObjectURL(url);
      dialog.close();
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
    };
  });
</script>

<dialog bind:this={dialog} class="awsum-photo-dialog" aria-labelledby="awsumPhotoTitle"
  oncancel={(event) => { event.preventDefault(); event.stopPropagation(); if (!processing) oncancel(); }}
  onkeydown={(event) => event.stopPropagation()} onkeyup={(event) => event.stopPropagation()}>
  <header>
    <h2 id="awsumPhotoTitle">Adjust Image</h2>
    <button type="button" class="close" aria-label="Cancel image adjustment" disabled={processing} onclick={oncancel}>&times;</button>
  </header>
  <p>Zoom out to fit the whole image or zoom in to crop. Drag or use arrow keys to position.</p>
  <canvas bind:this={canvas} width={frame} height={frame} tabindex="0"
    aria-label="Square image crop. Drag or use arrow keys to reposition."
    onpointerdown={start} onpointermove={move} onpointerup={stop} onpointercancel={stop}
    onlostpointercapture={stop} onkeydown={keydown}></canvas>
  {#if !ready && !error}<div role="status">Loading image...</div>{/if}
  <label class="zoom">Zoom
    <input aria-label="Image zoom" type="range" min={imageConfig.minZoom} max={maxZoom} step="0.01" value={zoom}
      disabled={!ready || processing} oninput={(event) => { zoom = Number(event.currentTarget.value); draw(); }} />
  </label>
  <div class="error" role="alert">{error}</div>
  <footer>
    <button type="button" disabled={!ready || processing} onclick={reset}>Reset</button>
    <span></span>
    <button type="button" disabled={processing} onclick={oncancel}>Cancel</button>
    <button type="button" class="apply" disabled={!ready || processing} onclick={apply}>{processing ? 'Processing...' : 'Apply'}</button>
  </footer>
</dialog>

<style>
  .awsum-photo-dialog { box-sizing: border-box; width: min(420px, calc(100vw - 24px)); max-height: calc(100dvh - 24px); overflow: auto; margin: auto; padding: 20px; border: 1px solid #dfe6ee; border-radius: 12px; box-shadow: 0 20px 60px #0f172a40; background: white; color: #334155; font: 13px system-ui, sans-serif; }
  .awsum-photo-dialog::backdrop { background: #0f172a99; }
  header, footer { display: flex; align-items: center; gap: 8px; }
  h2 { font-size: 18px; color: #172f57; margin: 0; flex: 1; }
  p { color: #64748b; font-size: 12px; }
  canvas { display: block; width: min(300px, 100%); height: auto; aspect-ratio: 1; margin: 16px auto; outline: 2px solid #94a3b8; border-radius: 2px; touch-action: none; cursor: grab; background: #f1f5f9; }
  canvas:active { cursor: grabbing; }
  canvas:focus-visible { outline: 3px solid #2563eb; }
  .zoom { display: flex; align-items: center; gap: 12px; }
  input { flex: 1; min-width: 0; accent-color: #2563eb; }
  .error { min-height: 18px; margin: 8px 0; color: #b42332; }
  footer span { flex: 1; }
  button { border: 1px solid #cbd5e1; border-radius: 6px; padding: 7px 12px; color: #2459a8; background: white; font: inherit; cursor: pointer; }
  button.apply { color: white; background: #2459a8; border-color: #2459a8; }
  button.close { border: none; font-size: 22px; padding: 0 4px; }
  button:disabled { opacity: .5; cursor: default; }
</style>
