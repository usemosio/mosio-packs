# mosio-packs

Versioned, hash-pinned asset packs for the [Mosio](https://github.com/usemosio/mosio) engine. Packs are published as GitHub Release assets and are not stored in Git.

| Release tag | Contents | Fetched by |
|---|---|---|
| `registry-<sha8>` | Registry pack (components, templates, icons, fonts) | `mosio registry fetch` |
| `sfx-<sha8>` | Curated sound-effects pack | `mosio media sfx fetch` |
| `modnet-<sha8>` | MODNet portrait matting model (ONNX) | `mosio media cutout --fetch` |
| `birefnet-lite-<sha8>` | BiRefNet_lite segmentation model (ONNX, optional) | `mosio media cutout --fetch` |
| `onnxruntime-<version>-<sha8>` | ONNX Runtime CPU build (linux-x64) | `mosio media cutout --fetch` |

`<sha8>` is the first 8 hex characters of the sha256 of the main asset. The engine pins the full sha256 of every pack in its `toolchain.lock.json` and verifies it on download and on use. Assets are immutable per tag: new bytes get a new tag, and existing assets are never replaced.

Verify by hand: `sha256sum <file>` and compare with the hash in the release notes.

## Licenses and notices

Every pack is published together with its own license and notice files, taken from the sources it contains. Licenses present are MIT, Apache-2.0, CC0-1.0 and the SIL Open Font License (OFL), depending on the item.

- Registry pack: `NOTICES/` inside the archive lists each item's license and origin; `NOTICES/LICENSE` is the pack license.
- SFX pack: `LICENSE`, `NOTICE` and per-source `LICENSE-*.txt` inside the archive. Sources: [earshot](https://github.com/Leonxlnx/earshot) sound library (MIT) and [Kenney](https://kenney.nl/assets) audio packs (CC0-1.0).
- MODNet: [ZHKKKe/MODNet](https://github.com/ZHKKKe/MODNet), Apache-2.0 (code and models); ONNX export from [Xenova/modnet](https://huggingface.co/Xenova/modnet) (Apache-2.0).
- BiRefNet_lite: [ZhengPeng7/BiRefNet](https://github.com/ZhengPeng7/BiRefNet), MIT; ONNX export from [onnx-community/BiRefNet_lite-ONNX](https://huggingface.co/onnx-community/BiRefNet_lite-ONNX) (MIT).
- ONNX Runtime: [microsoft/onnxruntime](https://github.com/microsoft/onnxruntime), MIT; the official NuGet package is redistributed unmodified with its `LICENSE` and `ThirdPartyNotices.txt`.

Model and runtime releases attach a `*-LICENSE.txt` and `*-NOTICE.txt` next to the file. All files are redistributed byte for byte as published by their sources. Rights remain with the original authors; check the license of an item before reusing it outside the Mosio engine.

## `@gfonts` registry data

`registry/gfonts/` is the data of the built-in `@gfonts` namespace of the engine: every [Google Fonts](https://github.com/google/fonts) family with a latin subset as a `mosio:font` item (`font-<kebab-family>.json`), an index (`registry.json`) and `manifest.json` (the pinned `google/fonts` commit, counts, every skipped family with its reason). Font files are not stored here: each face is a URL below `raw.githubusercontent.com/google/fonts/<commit>/` with its sha256 and size, and the licence text (OFL, Apache or UFL) is inline. Regenerate with `python3 -I scripts/gfonts/generate.py --sha <commit> --workdir <fresh dir>`; unchanged families are left as they are and changed ones get the next patch version. Reserved Font Names are kept unrenamed and listed per item.
