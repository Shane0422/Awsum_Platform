import configuration from '../../../../config/image.json';

export const imageConfig = Object.freeze(configuration);

export function validateImage(file: File) {
  if (!file.name || file.name !== file.name.trim() || file.name.startsWith('.') ||
      /[\/\\:%?#<>"|*\x00-\x1f\x7f]/.test(file.name) || file.name.includes('..') ||
      /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(file.name)) {
    throw new Error('Use a simple image filename without directories.');
  }
  if (!/\.(jpe?g|png|webp)$/i.test(file.name) || !imageConfig.inputMimeTypes.includes(file.type)) {
    throw new Error('Choose a JPG, JPEG, PNG or WEBP image.');
  }
  if (!file.size || file.size > imageConfig.maxInputBytes) {
    throw new Error(`Image must be non-empty and no larger than ${imageConfig.maxInputBytes / 1024 / 1024} MB.`);
  }
}

export async function processImage(image: HTMLImageElement, crop: { x: number; y: number; size: number }): Promise<File> {
  const output = document.createElement('canvas');
  output.width = imageConfig.width;
  output.height = imageConfig.height;
  const context = output.getContext('2d');
  if (!context) throw new Error('Image processing is not supported in this browser.');
  context.imageSmoothingQuality = 'high';
  context.fillStyle = imageConfig.background;
  context.fillRect(0, 0, output.width, output.height);
  const scale = output.width / crop.size;
  context.drawImage(image, -crop.x * scale, -crop.y * scale,
    image.naturalWidth * scale, image.naturalHeight * scale);
  const mime = `image/${imageConfig.format}`;
  const blob = await new Promise<Blob | null>(resolve => output.toBlob(resolve, mime, imageConfig.quality));
  if (!blob || blob.type !== mime) throw new Error('This browser cannot create WEBP images. Use a current browser.');
  return new File([blob], `image.${imageConfig.format}`, { type: mime });
}
