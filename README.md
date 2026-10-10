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

# Run the browser UI (open the printed URL).
polyglot serve --bind 0.0.0.0 --port 8765
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
polyglot serve [--bind 0.0.0.0] [--port 8765]
polyglot update [--check | --yes] [--branch BRANCH] [--json]
```

`polyglot update --check` checks the latest commit on GitHub without installing it; use `--branch BRANCH` to select a branch. `polyglot update` installs from a commit-pinned GitHub archive with the active Python interpreter's `pip` (use `--yes` to skip confirmation). For a local Git checkout, `python updater.py`, `./updater.sh`, or `updater.bat` fetches and fast-forwards the checkout from GitHub; it refuses dirty or diverged worktrees. No GitHub Release is required. See [`docs/UPDATING.md`](docs/UPDATING.md).

### Embedding modes

- `append` (default) puts a ZIP payload bundle at the end for interoperability with prefix-tolerant archive tools. Where the host defines an extensible slot, it also stores a small native manifest mirror. MP4 uses a legal `free` box. Info-ZIP may warn about the self-extracting prefix and return warning status even when its entries test cleanly; strict ZIP-only readers may reject a polyglot prefix.
- `native` puts the complete store inside a host-specific legal slot. ZIP interoperability depends on whether the container's own readers tolerate the prefix/slot; `polyglot unpack` handles the generated files.
- `none` writes just the selected host/layers; payloads are deliberately not stored. This is rarely useful for a make/unpack workflow.
- `--store raw` uses PolyglotMaker's framed raw store instead of ZIP.
- `--compress` selects the ZIP compression method. TAR-family hosts use their matching outer compression.

`--also jpeg,mp4` adds complete additional file structures after the host. They are scanner-visible **separate layers**, not formats valid at byte offset zero. `inspect` labels those honestly as overlays/layers.

`--obfuscate KEY` applies a SHA-256 counter-keystream XOR to the bundle. This is **obfuscation, not encryption**; it is not suitable for protecting secrets. Keep the key separately if you need to unpack the bundle.

## Web UI

The web app is served by `polyglot serve` using `http.server`; it needs no Flask, Node, database, or CDN. It supports drag-and-drop creation, generated or first-file byte-exact hosts, download, inspect with an SVG byte map, unpack, and a searchable format catalog. The server binds to `0.0.0.0` by default and accepts same-origin reverse-proxy preview hosts.

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
