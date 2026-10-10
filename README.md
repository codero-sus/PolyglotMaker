# PolyglotMaker

**Easy to use Polyglot Maker yet too much advance.**

PolyglotMaker combines files of different types into one output, then restores the original payload bytes on unpack. A PNG, MP4, executable, PDF, text document, ZIP, or another supported host can carry files of **any type**; inputs are kept byte-for-byte and SHA-256 checked. It includes both a Python CLI and a small browser UI.

> **Responsible use:** use for legitimate single-file distribution, format research, steganography demonstrations, and CTFs. This is not an antivirus-evasion or malware-concealment tool. Do not disguise malicious files or bypass another party's security controls.

## Quick start

Requires Python 3.10 or newer. **Runtime dependencies: Python standard library only.**

```sh
python -m pip install .

# Mix any files; host is selected from the first recognizable input.
polyglot make photo.png song.mp3 notes.pdf -o collection.png

# Inspect the layers and byte map, verify checksums, restore payloads.
polyglot inspect collection.png
polyglot verify collection.png
polyglot unpack collection.png -d restored/

# Run the browser UI on this computer (loopback by default).
polyglot serve --port 8765

# Optional: allow devices on your trusted local network to connect.
polyglot serve --lan --port 8765
```

For a quick source checkout without installing:

```sh
python -m polyglotmaker formats
python -m polyglotmaker make photo.png report.pdf -o result.png
```

## What “supports all types” means

- **Payload support is format-agnostic.** Any file that can be read as bytes can be added. Images, recordings, office files, scripts, executables, archives, databases, fonts, and unknown binary formats are never transcoded or executed.
- **Host generators** create minimal format-valid carriers across 63 registered keys: images, audio/video, documents, code/text, executables, ZIP/TAR, EPUB, JAR/APK-like, OOXML, and OpenDocument packages. See [`docs/FORMATS.md`](docs/FORMATS.md) and `polyglot formats`.
- **Real host files can be preserved.** Use `--host-file existing.png` with `--host png` to retain the original host bytes and append a bundle, while also including the original file in the restorable payload list. Strict formats that cannot legally tolerate appended bytes are rejected rather than silently corrupted.
- Some hosts are intentionally **minimal demonstrations**, not media transcoding or full office-authoring tools. The generated APK is unsigned and not installable; generated office skeletons should be checked in the target office suite before distribution. For maximum compatibility, pass a real host file.

## CLI overview

```text
polyglot make FILE... [-o OUT] [--host auto|FORMAT] [--host-file PATH]
  [--also FORMAT[,FORMAT...]] [--embed-mode append|native|none]
  [--store zip|raw] [--compress none|deflate|bzip2|lzma] [--obfuscate KEY]

polyglot unpack FILE [-d DIR] [--key KEY] [--list] [--select MEMBER] [--overwrite]
polyglot inspect FILE [--json]
polyglot verify FILE [--key KEY] [--json]
polyglot formats [--category CATEGORY] [--json]
polyglot doctor [--json]
polyglot serve [--lan | --bind ADDRESS] [--port 8765]
polyglot update [--check | --yes] [--json]
```

`polyglot update` and the root `updater.py` / `updater.sh` / `updater.bat` update **only** from GitHub branch `arena/a02bfaaf-polyglotmaker`. The CLI installs a commit-pinned archive; the root scripts fast-forward a clean local Git checkout and refuse dirty or diverged worktrees. Use `--check` to preview; `polyglot update --yes` installs without prompting. No GitHub Release is required. Interpreter selection and details are in [`docs/UPDATING.md`](docs/UPDATING.md).

### Embedding modes

- `append` (default) puts a ZIP payload bundle at the end for interoperability with prefix-tolerant archive tools. Where the host defines an extensible slot, it also stores a small native manifest mirror. MP4 uses a legal `free` box. Info-ZIP may warn about the self-extracting prefix and return warning status even when its entries test cleanly; strict ZIP-only readers may reject a polyglot prefix.
- `native` puts the complete store inside a host-specific legal slot. ZIP interoperability depends on whether the container's own readers tolerate the prefix/slot; `polyglot unpack` handles the generated files.
- `none` writes just the selected host/layers; payloads are deliberately not stored. This is rarely useful for a make/unpack workflow.
- `--store raw` uses PolyglotMaker's framed raw store instead of ZIP.
- `--compress` selects the ZIP compression method. TAR-family hosts use their matching outer compression.

`--also jpeg,mp4` adds complete additional file structures after the host. They are scanner-visible **separate layers**, not formats valid at byte offset zero. `inspect` labels those honestly as overlays/layers.

`--obfuscate KEY` applies a SHA-256 counter-keystream XOR to the bundle. This is **obfuscation, not encryption**; it is not suitable for protecting secrets. Keep the key separately if you need to unpack the bundle.

## Web UI

The web app is served by `polyglot serve` using `http.server`; it needs no Flask, Node, database, or CDN. It supports drag-and-drop creation, generated or first-file byte-exact hosts, download, inspect with an SVG byte map, unpack, and a searchable format catalog. The server listens on this computer only by default. Use `polyglot serve --lan` to allow devices on your local network; LAN mode has no sign-in, so use it only on a trusted private network and never expose it to the public internet. Same-origin reverse-proxy preview hosts are supported.

## Format integrity and safety

- Each payload is recorded with original name, byte length, MIME guess, mode, mtime, and SHA-256.
- The ZIP comment contains a compact bootstrap footer; the ZIP remains at EOF in append mode for archive-tool compatibility.
- Unpack validates sizes/checksums, sanitizes paths against zip-slip traversal, and avoids overwriting by default.
- Generated PE files import `kernel32!ExitProcess`; generated ELF stubs exit with status 0. **PolyglotMaker itself never runs input files or extracted payloads.**
- Optional test-time validators (Pillow, pypdf, pefile, mutagen) are not runtime requirements. Run `pip install '.[test]'` to install them, then `pytest`.

## Development

```sh
make test
make doctor
make serve
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/SAFETY.md`](docs/SAFETY.md), and [`docs/WEB.md`](docs/WEB.md).
