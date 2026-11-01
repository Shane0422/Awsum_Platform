# AWSUM common image components

Source for the AWSUM Common Image Framework browser bundle (ImageUploader,
ImageCropper/Adjuster, Avatar). It is backend-owned and used by server templates.

- Configuration: `../../config/image.json`
- Build output: `../../static/components/image-uploader/` (`image-uploader.js`, `image-uploader.css`, global `AwsumImages`)

```powershell
cd shared\image-components
npm.cmd ci
npm.cmd run check
npm.cmd run build
```

Do not create screen-specific image upload/crop logic; extend these components.
See `docs/AWSUM_STORAGE_STANDARD.md` and `docs/AWSUM_PROJECT_ARCHITECTURE.md`.
