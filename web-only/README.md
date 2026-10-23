# PolyglotMaker Web-only edition

This folder is a standalone, static, browser-only version. It does not need Python, Node, a local server, an API, a CDN, or an internet connection for file processing.

## Run it

Open `index.html` directly in a current browser, or place the whole `web-only` folder on any static web host. Keep the local assets together. File processing never uploads data or calls a remote service. When opened from `file://`, the app works without a service worker; when served from HTTPS or localhost, it can cache only its static app shell for offline reopening and offer installation.

## What it can do

- Add individual files or whole folders; folder-relative paths are kept inside the bundle.
- Create an appended, uncompressed ZIP bundle with a PolyglotMaker v1 manifest and integrity footer.
- Use a built-in valid 1×1 PNG shell, or append the bundle to a host file you choose.
- Keep arbitrary payload bytes intact, with SHA-256 checksums and safe member names. Archive paths are cleaned for Windows device names and trailing-dot/space rules; conflicting paths are disambiguated across case-insensitive and Unicode-normalizing filesystems, with any renamed paths shown before download.
- Unpack appended ZIP bundles created by this edition or by PolyglotMaker's normal ZIP/append mode. Search restored names, select the files you want, save files individually, or build a ZIP of the selection.
- Search and sort large payload/restored-file lists by name, MIME type, or size without flooding the page; at most 150 matching rows render at once.
- Choose safe output filenames for new bundles and restored ZIPs, see an estimated size/file-count budget, and use select-all, select-matches, clear, and invert controls for restored files. The selection summary includes selected bytes and current match count.
- Copy bundle and per-file SHA-256 digests, or save a JSON verification report containing file metadata (never file contents).
- See live progress for long operations and cancel a build, restore, or ZIP export in progress.

Theme, sort, output-name, and host-inclusion preferences can be remembered locally; selected files, source paths, and file contents are never persisted. The service worker caches only versioned static app assets, not user data.

ZIP output is assembled in a single preallocated buffer; the custom host is read once, extraction reuses stored-data views, and Deflate output streams into its final buffer to reduce peak memory. The browser edition limits containers, extracted data, and ZIP downloads to 128 MiB and supports up to 10,000 files. It does not generate all 63 host formats and currently does not unpack raw-store, obfuscated, encrypted, ZIP64, or native-only bundles; use the Python CLI/server for those. Appending to a custom strict-format host can make that host invalid—the built-in PNG shell is the recommended default.

## Author & credits

- **Author and all project credits:** [codero-sus](https://github.com/codero-sus)
- **Website:** [https://the-codero.web.app/](https://the-codero.web.app/)
- Made with ❤️ by [codero-sus](https://github.com/codero-sus).
