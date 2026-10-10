# Format catalog

`polyglot formats` prints the live registry. The list below is the set of **host generators** in this version (63 keys). It is distinct from payload support: arbitrary input bytes can be bundled regardless of extension or signature.

## Images — 8

| Key | Output | Native slot / notes |
|---|---|---|
| `png` | `.png` | Private ancillary `plGm` chunk before image data; valid RGB PNG |
| `jpeg` (`jpg`) | `.jpg` | Chained JPEG `COM` segments; baseline grayscale image |
| `gif` | `.gif` | GIF comment extension; valid GIF89a image |
| `bmp` | `.bmp` | 24-bit BMP; append-mode overlay is ignored by typical decoders |
| `webp` | `.webp` | Valid VP8L lossless image and unknown RIFF `PLGM` chunk |
| `tiff` (`tif`) | `.tif` | TIFF IFD with private tag `0x83d0` |
| `ico` | `.ico` | PNG-compressed icon resource; append-mode overlay |
| `tga` | `.tga` | TGA 2.0 developer directory entry `0xc0de` |

## Audio / video — 7

| Key | Output | Native slot / notes |
|---|---|---|
| `wav` | `.wav` | PCM16 silence and RIFF `PLGM` chunk |
| `aiff` | `.aiff` | AIFF PCM silence and base64-encoded `ANNO` payload |
| `midi` (`mid`) | `.mid` | Standard MIDI file; sequencer-specific meta event |
| `mp3` | `.mp3` | ID3v2.4 `PRIV` frame plus silent MPEG-1 Layer III frames |
| `flac` | `.flac` | FLAC STREAMINFO, Vorbis comment, and constant subframe silence |
| `mp4` (`mov`, `m4v`, `m4a`) | `.mp4` | ISO BMFF `ftyp` + `moov`; legal `free` boxes |
| `avi` | `.avi` | RIFF AVI with one uncompressed 1×1 video frame and optional metadata chunk |

These are minimal demo hosts, not media converters. Real audio/video can be supplied with `--host-file` to preserve the original bitstream byte-for-byte.

## Documents and markup


| Key | Output | Notes |
|---|---|---|
| `pdf` | `.pdf` | PDF 1.7 page with an EmbeddedFiles attachment; append-mode ZIP is also tolerated by common readers |
| `postscript` (`ps`) | `.ps` | PostScript Level 2 one-page document with a DSC comment payload |
| `rtf` | `.rtf` | RTF text host; bundle is embedded in a legal destination group |

## Executables / objects — 6

| Key | Output | Notes |
|---|---|---|
| `exe` (`pe`, `pe32`, `pe64`) | `.exe` | PE32/PE32+ x86; imports `kernel32!ExitProcess`; data follows the last section |
| `elf` | `.elf` | ELF32/ELF64 x86; static `exit(0)` stub; data follows the load segment |
| `mz` | `.mz` | Minimal DOS MZ program that exits through INT 21h |
| `wasm` | `.wasm` | WebAssembly v1 module with custom section |
| `class` | `.class` | Java 8 class structure with a custom class attribute |
| `macho` | `.o` | Minimal x86-64 Mach-O relocatable-object header; not a linked app |

Executable hosts are inert stubs. Only generated stub instructions execute; input payloads are not run. The Windows PE output is not executable on Linux, and the Linux ELF output is architecture/platform-specific.

## Archives and packages — 15

| Keys | Outputs | Notes |
|---|---|---|
| `zip`, `tar` | `.zip`, `.tar` | Inputs stored as native `payloads/` members with a manifest |
| `gz`, `bz2`, `xz` | `.tar.gz`, `.tar.bz2`, `.tar.xz` | Real compressed TAR streams; avoids invalid trailing bytes |
| `jar`, `apk` | `.jar`, `.apk` | ZIP packages with minimal manifest/Android manifest; APK is unsigned and not installable |
| `docx`, `xlsx`, `pptx` | `.docx`, `.xlsx`, `.pptx` | Minimal OOXML package skeleton plus `payloads/` members |
| `epub` | `.epub` | EPUB 3 skeleton; uncompressed `mimetype` is first entry |
| `odt`, `ods`, `odp` | matching OpenDocument suffix | Minimal package skeleton; uncompressed `mimetype` is first entry |
| `cbz` | `.cbz` | ZIP comic/resource archive |

The office/ebook package skeletons are for format demonstrations and payload aggregation; target-application acceptance should be tested before relying on them as authored documents.

## Text / code / data — 26

`text` (`.txt`), `markdown` (`.md`), `html` (`.html`), `svg` (`.svg`), `json` (`.json`), `xml` (`.xml`), `yaml` (`.yaml`), `csv` (`.csv`), `ini` (`.ini`), `toml` (`.toml`), `css` (`.css`), `javascript` (`.js`), `typescript` (`.ts`), `python` (`.py`), `shell` (`.sh`), `batch` (`.bat`), `powershell` (`.ps1`), `rtf` (`.rtf`), `sql` (`.sql`), `java` (`.java`), `c` (`.c`), `php` (`.php`), `ruby` (`.rb`), `go` (`.go`), `rust` (`.rs`), and `postscript` (`.ps`).

Some keys in this subsection share the document, executable, or image category, so counts by category are not additive. Strict formats (JSON/XML/source files) place the full bundle in a base64 property/comment so the host remains syntactically valid; the pipeline automatically selects native mode in those cases.

## Layer support

Any registered host may be requested with `--also FORMAT`. A layer is a separately generated valid file, but it is **not** claimed to be valid from offset zero in the combined output. Some strict host formats reject extra appended layers because doing so would corrupt the host syntax.
