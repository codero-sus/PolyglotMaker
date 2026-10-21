# Browser-only improvement program

**Target:** 777+ substantive, testable improvements. **Current delivered count: 19 (19/777 minimum).** This is a running count, not a claim that the target is finished. Only shipped behavior is counted; the tracker will not be padded with cosmetic renames or duplicate bullets.

## Batch 1 — 19 shipped improvements

1. Added a user-selectable dark theme.
2. The initial theme follows the operating system when no preference has been chosen.
3. The chosen theme persists between visits.
4. Theme changes update the browser chrome color.
5. Create-list sort order persists between visits.
6. Restore-list sort order persists between visits.
7. The output-name preference persists between visits.
8. The “include host” preference persists between visits.
9. Preferences are stored separately from input data.
10. Preference storage contains no selected file bytes or source file paths.
11. Added a web-app manifest for installable browsers.
12. Added scalable SVG and raster app icons.
13. Added Apple home-screen metadata and a touch icon.
14. Added an install prompt when the browser supports it.
15. Installation completion is reported in the app.
16. Added a same-origin service worker for app-shell caching.
17. The service worker caches only the app’s static assets, never selected files or downloads.
18. Cached app assets allow the static app to reopen offline after the first successful visit.
19. Added keyboard shortcuts for switching tools, focusing filters, opening help, and clearing/closing with Escape.

**Verified in this batch:** JavaScript syntax, static asset availability, manifest/PNG structure, offline-cache asset list, and existing Python/Node integration suite. The Python CLI and backend-driven interface were not changed.
