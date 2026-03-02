# HITL MCP Server — Image Tools Implementation Prompt

## Goal

Add two new MCP tools to `hitl_mcp_server.py` that let the agent **view images from the local filesystem**. These tools give the agent "eyes" on any file the user points to, without needing the user to manually send screenshots or photos.

---

## Tools to Implement

### Tool 1: `get_image`

**Purpose:** Read a single image file from disk and return it as MCP image content so the LLM can see it.

**Parameters:**
| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `file_path` | `str` | yes | — | Absolute or relative path to the image file |
| `max_size` | `int` | no | `1400` | Max dimension in px (longest side). Resize if larger. |
| `run_ocr` | `bool` | no | `True` | Extract text via OCR if OCR is available |

**Behavior:**
1. Validate the file exists and is a recognized image format (`.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.webp`, `.tiff`, `.svg`)
2. Read the file into bytes
3. If the image exceeds `max_size` on either dimension, resize proportionally (use PIL)
4. Base64-encode the (possibly resized) image
5. If `run_ocr=True` and OCR is available, run `_extract_ocr_from_image_bytes()` on it
6. Return `[TextContent(metadata), ImageContent(b64)]` — same pattern as `get_window_screenshot`

**Return metadata (TextContent JSON):**
```json
{
  "success": true,
  "file_path": "/absolute/path/to/image.png",
  "file_name": "image.png",
  "original_width": 1920,
  "original_height": 1080,
  "returned_width": 1400,
  "returned_height": 788,
  "file_size": 245000,
  "mime_type": "image/png",
  "was_resized": true,
  "ocr_enabled": true,
  "ocr_available": true,
  "ocr_text": "detected text...",
  "ocr_lines": [...],
  "ocr_avg_confidence": 0.92
}
```

**Edge Cases:**
- File not found → `{"success": false, "error": "File not found: ..."}`
- Unsupported extension → `{"success": false, "error": "Unsupported image format: .xyz"}`
- PIL not available → Skip resize, return raw bytes (warn in metadata)
- OCR not available → include `"ocr_available": false`, skip OCR
- SVG → Return as-is (no resize), mime `image/svg+xml`

---

### Tool 2: `list_images`

**Purpose:** Scan a folder for image files and return previews + metadata. Lets the agent browse a folder like a gallery.

**Parameters:**
| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `folder_path` | `str` | yes | — | Absolute or relative path to the folder to scan |
| `pattern` | `str` | no | `"*"` | Glob pattern to filter filenames (e.g. `"screenshot*"`, `"*.png"`) |
| `limit` | `int` | no | `5` | Max number of images to return (1–20) |
| `max_size` | `int` | no | `800` | Max dimension per thumbnail (smaller than `get_image` default since we return multiple) |
| `sort_by` | `str` | no | `"modified"` | Sort order: `"modified"` (newest first), `"name"` (alphabetical), `"size"` (largest first) |
| `run_ocr` | `bool` | no | `False` | Run OCR on each image (default off since it's slow for batches) |
| `recursive` | `bool` | no | `False` | Scan subdirectories recursively |

**Behavior:**
1. Validate folder exists
2. Scan for files matching image extensions (`.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.webp`, `.tiff`)
3. Apply `pattern` glob filter
4. Sort by `sort_by` criterion
5. Take first `limit` images
6. For each image: read, resize to `max_size`, base64 encode, optionally OCR
7. Return `[TextContent(summary), ImageContent(img1), ImageContent(img2), ...]`

**Return metadata (TextContent JSON):**
```json
{
  "success": true,
  "folder_path": "/absolute/path/to/folder",
  "total_images_found": 23,
  "images_returned": 5,
  "pattern": "*",
  "sort_by": "modified",
  "images": [
    {
      "file_name": "screenshot_01.png",
      "file_path": "/absolute/path/to/folder/screenshot_01.png",
      "width": 800,
      "height": 450,
      "file_size": 145000,
      "mime_type": "image/png",
      "modified": "2026-03-02T14:30:00",
      "ocr_text": ""
    }
  ]
}
```

**Edge Cases:**
- Folder not found → `{"success": false, "error": "Folder not found: ..."}`
- No images found → `{"success": true, "total_images_found": 0, "images_returned": 0, "images": []}`
- Limit clamped to 1–20 range
- Permission errors on individual files → Skip file, add to `"skipped"` list

---

## Toggleability / Feature Flag

Follow the existing `HITL_OCR_ENABLED` pattern. Add:

```
HITL_IMAGE_TOOLS_ENABLED=true|false   (default: true)
```

### Implementation:

```python
def is_image_tools_enabled() -> bool:
    """Check whether local image viewing tools are enabled."""
    value = os.getenv("HITL_IMAGE_TOOLS_ENABLED", "true").strip().lower()
    return value in ("true", "1", "yes", "on")
```

If disabled, both tools return:
```json
{
  "success": false,
  "error": "Image tools are disabled. Set HITL_IMAGE_TOOLS_ENABLED=true to enable."
}
```

### VS Code MCP config (`.vscode/mcp.json`):

Users can add this env var to toggle:
```json
"env": {
  "HITL_IMAGE_TOOLS_ENABLED": "true"
}
```

---

## Compatibility Notes

### Dependencies
- **Required:** None (base64, os, io are stdlib)
- **Optional:** PIL/Pillow — for resize. Already imported conditionally in the server (`from PIL import Image as PILImage`)
- **Optional:** RapidOCR — for text extraction. Already imported conditionally.

### Platform
- Works on Windows, macOS, Linux (just filesystem + stdlib)
- Path handling: Use `os.path.abspath()`, `os.path.expanduser()`, `os.path.normpath()`
- Slash normalization: Accept both `/` and `\` on Windows

### MCP Protocol
- Use the exact same `[TextContent, ImageContent]` return pattern as `get_window_screenshot` (line ~3045)
- Multiple images: Return `[TextContent, ImageContent, ImageContent, ...]` — MCP supports content arrays
- MIME detection: Use file extension mapping (no need for `python-magic`)

### MIME Type Map:
```python
_IMAGE_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".svg": "image/svg+xml",
}
```

---

## Code Placement

Insert the two new tools **after** `get_window_screenshot` and **before** `show_info_message`:

```
@mcp.tool()  get_window_screenshot  (line ~3045)
                                     ← INSERT HERE
@mcp.tool()  get_image              (NEW)
@mcp.tool()  list_images            (NEW)

@mcp.tool()  show_info_message      (line ~3096)
```

Also add a helper function near the other helpers (around line ~516):

```python
def is_image_tools_enabled() -> bool:
    ...

_IMAGE_MIME_MAP = { ... }

_SUPPORTED_IMAGE_EXTENSIONS = set(_IMAGE_MIME_MAP.keys())

def _read_and_resize_image(file_path: str, max_size: int = 1400) -> Dict[str, Any]:
    ...
```

---

## Updates to Existing Code

1. **`health_check` tool** — Add to the tools_available list:
   ```python
   "tools_available": [
       ...existing...,
       "get_image",
       "list_images"
   ]
   ```
   Also add image tools status:
   ```python
   "image_tools": {
       "enabled": is_image_tools_enabled(),
       "pil_available": PILImage is not None,
       "ocr_available": _OCR_IMPORTED
   }
   ```

2. **`main()` startup** — Add print line:
   ```python
   img_state = "ENABLED" if is_image_tools_enabled() else "DISABLED"
   pil_state = "available" if PILImage else "NOT available (install Pillow for resize)"
   print(f"Image tools: {img_state} (PIL: {pil_state})")
   ```

3. **README.md** — Add section documenting the two new tools, parameters, and env var toggle.

---

## Testing Checklist

After implementation, verify:

- [ ] `get_image` with a valid PNG → returns image content the LLM can see
- [ ] `get_image` with max_size smaller than original → image is resized
- [ ] `get_image` with nonexistent file → returns error JSON
- [ ] `get_image` with `.txt` file → returns unsupported format error
- [ ] `get_image` with OCR on a screenshot containing text → OCR text populated
- [ ] `list_images` on a folder with 10 PNGs, limit=3 → returns exactly 3 images
- [ ] `list_images` with pattern `"screenshot*"` → only matching files
- [ ] `list_images` with sort_by `"modified"` → newest first
- [ ] `list_images` on empty folder → success with 0 images
- [ ] `HITL_IMAGE_TOOLS_ENABLED=false` → both tools return disabled error
- [ ] Health check shows image tools status
- [ ] Server starts without errors
- [ ] No new pip dependencies required

---

## Summary

| What | Impact |
|------|--------|
| New tools | 2 (`get_image`, `list_images`) |
| New helper functions | 3 (`is_image_tools_enabled`, `_IMAGE_MIME_MAP`, `_read_and_resize_image`) |
| New dependencies | 0 (PIL already optional-imported) |
| Lines of code | ~120-150 |
| Env vars | 1 (`HITL_IMAGE_TOOLS_ENABLED`) |
| Breaking changes | 0 |
