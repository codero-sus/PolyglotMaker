# Architecture

## Pipeline

A generated output has a host format, optional additional file layers, and a lossless payload store.

1. Read inputs into `InputFile` records. Payload bytes are opaque: they are not transcoded or executed.
2. Select a host (explicit `--host`, or `auto` from signatures/extensions).
3. Create a schema-v1 `POLYGLOTMAKER/manifest.json` with names, sizes, MIME guesses, modes, mtimes, roles, and SHA-256 hashes.
4. Build a store:
   - ZIP by default, with manifest first, inputs under `payloads/`, and a compact bootstrap footer in the ZIP comment.
   - PolyglotMaker's framed raw store with `--store raw`.
   - For ZIP/TAR/compressed TAR/EPUB/office-package hosts, inputs are native archive members.
5. Render the host. If the format has a legal extensible slot, the renderer places a manifest mirror there (`append`) or a complete bundle there (`native`). Strict syntaxes such as JSON/XML embed base64 so they remain valid.
6. Place `--also` file layers after the host. They are separate valid files at their own offsets; they are not represented as a second offset-zero signature.
7. Append the ZIP/raw bundle at EOF (or wrap it in a legal MP4 `free` box) to retain normal archive-tool interoperability.

## ZIP prefix and footer

ZIP readers that support self-extracting archives locate the end-of-central-directory record and adjust member offsets when an archive has a prefix. This makes a PNG/PDF/PE/ELF host followed by a ZIP readable by many archive tools. Info-ZIP may warn about the prefix and return a warning status while still listing/testing every entry; strict ZIP-only readers may reject it. Append mode leaves the ZIP at EOF and stores the bootstrap in the ZIP comment (max 65,535 bytes), rather than appending an unrelated trailer that can confuse archive tools.

The footer carries a version, absolute archive offset, archive length, SHA-256 of the manifest, SHA-256 of a normalized ZIP (its comment is zeroed for hashing), and flags. In the manifest, `bundle.sha256` is a deterministic digest of the ordered payload set (`name`, `size`, and per-file SHA-256); the footer is the authoritative checksum of ZIP records. A ZIP's own contents cannot recursively carry their final full-file hash without a self-referential definition.

## Host classes

`HostFormat` exposes:

- `render(HostContext) -> RenderResult`: write a complete host file and segment map.
- `wrap_trailing(bytes, context)`: put bytes into a format-specific legal wrapper (MP4 uses `free`).
- `parse(bytes)`: best-effort structural map for inspect.
- `extract_native(bytes)`: recover natively embedded metadata/bundles where possible.
- `validate(bytes)`: cheap self-validation.

Each format declares a `HostSpec`: extensions/aliases, suffix and prefix tolerance, legal embedding strategy, default store, and notes. The central registry is loaded explicitly by `formats/registry.py`.

## Unpack strategies

Unpack tries the bootstrap/ZIP store, raw store, native ZIP/TAR package members, host-native extraction, and manifest-only recovery. Every output name is normalized and checked to stay under the output directory. The manifest's size and SHA-256 are verified before data is written. Existing files are given unique names unless `--overwrite` is explicit.

## Dependencies

All package runtime code uses the standard library. Pillow, pypdf, pefile, and mutagen are optional test-only validators; the CLI and web server do not import them.
