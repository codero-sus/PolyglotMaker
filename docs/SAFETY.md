# Safety and responsible use

PolyglotMaker is intended for legitimate file aggregation, single-file distribution, format research, steganography demonstrations, and CTF challenges. It is **not** an AV-evasion tool and must not be used to conceal malicious files, disguise a payload as a trusted document, bypass security controls, or evade detection.

## Boundaries

- The tool stores inputs as opaque bytes and never executes them.
- Generated executable stubs contain only their documented exit behavior. Unpacking never launches restored files.
- `--obfuscate` is SHA-256 counter-stream XOR. It is deliberately labeled **obfuscation, not encryption**; a motivated analyst can recover the bytes. Do not use it for confidentiality.
- Real file formats can have parser-specific edge cases. Inspect output and test it in the intended viewer before distributing it.
- The browser UI accepts same-origin uploads to the local process and holds generated download bytes in process memory temporarily. Do not expose the web server to an untrusted network.
- Unpack rejects absolute paths, traversal components (`..`), control characters, and platform-reserved path components. It does not overwrite existing files by default.
- Keep malware research in isolated, authorized environments. Scan and review files under your organization's policies.

## Responsible-use note

The README, CLI help, and web UI carry this reminder: use only for authorized, benign work. PolyglotMaker does not promise that an output can bypass a scanner or that one signature is hidden from another parser.
