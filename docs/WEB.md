# Web UI and HTTP API

Start with:

```sh
polyglot serve --bind 0.0.0.0 --port 8765
```

The frontend uses relative same-origin URLs; it never calls a browser-side `localhost` service. The stdlib `ThreadingHTTPServer` serves static files and a JSON API. Uploads are held in memory and limited to 256 MiB per request. Generated downloads use a process-local cache capped at 512 MiB total; tokens expire when the server exits or when the bounded cache evicts old results. Larger generated outputs can be made with the CLI.

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Single-page browser app |
| GET | `/api/formats` | Registered host formats |
| GET | `/api/health` | Liveness/version |
| GET | `/api/download/{token}` | Download the latest generated file |
| POST | `/api/make` | Make; multipart form fields plus repeated `files` |
| POST | `/api/unpack` | Unpack; `file`, optional `key`, repeated `select` |
| POST | `/api/inspect` | Format detections and byte map |
| POST | `/api/verify` | Bundle and payload validation |

POST requests may be multipart/form-data (the browser's native upload format) or application/json containing base64 file data. Errors return a JSON object with an `error` string and a 4xx status where possible.

The UI includes drag-and-drop creation, a host and store selector, optional overlay layers, generated-file download, per-file unpack downloads, a searchable format catalog, and SVG offset visualization with hover tooltips.
