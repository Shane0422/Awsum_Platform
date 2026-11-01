import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: fileURLToPath(new URL('../../static/components/image-uploader', import.meta.url)),
    emptyOutDir: true,
    copyPublicDir: false,
    lib: {
      entry: fileURLToPath(new URL('./src/images.ts', import.meta.url)),
      name: 'AwsumImages',
      formats: ['iife'],
      fileName: () => 'image-uploader.js',
      cssFileName: 'image-uploader',
    },
  },
});
