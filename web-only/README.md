# PolyglotMaker Web-only edition

This folder is a standalone, static, browser-only version. It does not need Python, Node, a local server, an API, a CDN, or an internet connection for file processing.

## Run it

Open `index.html` directly in a current browser, or place the whole `web-only` folder on any static web host. Keep `index.html`, `styles.css`, `engine.js`, and `app.js` together. The web-only app makes no network requests; the optional static host only serves these files.

## What it can do

- Create an appended, uncompressed ZIP bundle with a PolyglotMaker v1 manifest and integrity footer.
- Use a built-in valid 1×1 PNG shell, or append the bundle to a host file you choose.
- Keep arbitrary payload bytes intact, with SHA-256 checksums and safe member names.
- Unpack appended ZIP bundles created by this edition or by PolyglotMaker's normal ZIP/append mode. Save individual files or download all restored files in one ZIP.

The browser-only edition has a 128 MiB selected-data limit. It does not generate all 63 host formats and currently does not unpack raw-store, obfuscated, encrypted, ZIP64, or native-only bundles; use the Python CLI/server for those. Appending to a custom strict-format host can make that host invalid—the built-in PNG shell is the recommended default.

## Author & credits

- **Author and all project credits:** [codero-sus](https://github.com/codero-sus)
- **Website:** [https://the-codero.web.app/](https://the-codero.web.app/)
- Made with ❤️ by [codero-sus](https://github.com/codero-sus).
