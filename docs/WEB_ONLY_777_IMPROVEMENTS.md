# Improvement program

**Target:** 777+ substantive, testable improvements. **Current delivered count: 44 (44/777 minimum).** This is a running count, not a claim that the target is finished. Only shipped behavior is counted; the tracker will not be padded with cosmetic renames or duplicate bullets.

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

## Batch 2 — 25 shipped improvements

1. Windows device basenames are made safe even when an extension is present, such as `CON.txt`.
2. Trailing dots and spaces are removed from archive path components for Windows compatibility.
3. Filename collision checks account for case-insensitive filesystems.
4. Collision keys account for canonically equivalent Unicode spellings without rewriting the original spelling unnecessarily.
5. The Python creator suffixes conflicting portable payload names without dropping file bytes.
6. The browser creator applies the same portable collision handling.
7. Browser restore assigns distinct safe names when an older bundle contains colliding manifest names.
8. ZIPs made from selected restored files also receive collision-safe member names.
9. ZIP validation rejects ambiguous filesystem-equivalent payload paths.
10. Raw-store parsing rejects colliding portable paths.
11. TAR-backed restore rejects colliding portable paths.
12. ZIP and TAR entries are indexed once for path lookup, avoiding a full archive rescan for every restored file.
13. Added “Select matches” for every file matching the current restore filter, including matches beyond the visible-row cap.
14. Added “Invert matches” without changing selections outside the current filter.
15. The selection summary reports selected bytes.
16. The selection summary reports the current number of filter matches.
17. Restored ZIP downloads now have an editable filename.
18. The default restored-ZIP filename is derived from the selected container.
19. Restored ZIP names use the existing cross-platform filename sanitizer and retain a `.zip` extension.
20. A user-edited restored-ZIP filename preference persists locally.
21. Added a keyboard-accessible “Skip to tools” link.
22. Selection changes are announced through a polite live region.
23. Dense selection controls wrap on narrow screens instead of forcing horizontal overflow.
24. The browser lists each filename it adjusts before download so users can see portability-driven changes.
25. Python CLI build results warn with examples when collision handling changes an input filename.

**Verified so far:** Python and Node round-trips for collision handling; 1,000-member bundle creation/restore; JavaScript syntax; static asset availability; manifest/PNG structure; offline-cache asset list; and the existing Python/Node integration suite. The browser processing path remains local-only.
