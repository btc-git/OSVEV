# OSVEV Refactored Module Structure

## Overview
The monolithic `osvev.py` file (1750 lines) has been refactored into modular components for better maintainability and collaboration with AI.

## Module Breakdown

### 1. **qt_compat.py** (~30 lines)
- PyQt6 compatibility layer
- Contains the `QtCompat` class that provides enums in a unified interface
- Centralizes all Qt constant mappings

### 2. **magnifier_label.py** (~85 lines)
- Custom `MagnifierLabel` widget
- Handles the draggable magnifying lens on video frames
- Features:
  - Lens rectangle painting with dashed border
  - Mouse event handling for dragging
  - Crosshair center point

### 3. **frame_processor.py** (~60 lines)
- Video frame processing utilities
- Static methods for:
  - Brightness adjustment (Y-channel only)
  - Contrast adjustment using lookup tables
  - Sharpness enhancement
  - Compression handling
  - Unified `process_frame()` method

### 4. **frame_info.py** (~180 lines)
- Frame metadata and information handling, including a cancellable worker
- Key features:
  - `FrameInfoHandler` class with static methods
  - FFprobe integration for video metadata
  - Frame data formatting for display
  - Speed factor calculations
  - Frame type descriptions

### 5. **osvev_app.py** (~1200 lines)
- Main `OSVEV` application class
- Contains:
  - UI setup and initialization
  - Video playback control
  - Frame processing pipeline
  - Zoomed view management
  - Pixel inspector functionality
  - Settings and event handlers
- Imports all other modules

### 6. **main.py** (~45 lines)
- Application entry point
- Handles:
  - QApplication initialization
  - High DPI settings
  - Theme setup
  - Application launch
  - Default verified PyAV input; explicit `--decoder opencv` legacy option without fallback

### 7. **pyav_input.py**
- Default, persistent verified input adapter
- Separate retained Y/U/V samples and explicit validated BGR conversion
- Exact integer-PTS seeking through the existing FFprobe metadata index
- No enhancement engine, preview renderer, video-frame cache, or exporter
- Imported lazily only when PyAV input is selected; errors never trigger fallback
- Decoder-flagged corrupt frames are refused, not treated as a compatibility permission

### 8. **playback_timing.py**
- Decoder-independent source timestamp timeline and monotonic deadlines
- Fractional frame intervals, speed changes, loop timing, and late-slot selection
- No decoding thread, media cache, or rendering engine
- Completed main-view paints feed bounded timing statistics in `osvev_app.py`

### 9. **ffmpeg_export.py**, **export_runner.py**, **export_dialog.py**
- One command/selection/conversion plan for PNG, FFV1 MKV, and lossy H.264
- Small modal command/copy/export interface with automatic verification;
  external-output verification retained in the API
- Cancellable export worker and bounded independent frame verification
- Shared libavfilter brightness/contrast/sharpening definitions for live processing
  and exact exports, followed by exact integer cropping in `crop_utils.py`
- Video export PTS/duration scaling with every selected frame retained;
  per-frame timing and container-duration verification
- Export-only scaling after crop uses the selected Zoom Level and Interpolation
  Method, with explicit FFmpeg/display differences; neither viewer changes
- JPEG, audio, highlights, Magnified View, Additional Zoom and overlays excluded
- Export does not require a preview cache or working copy. Optional timeline
  previews and compatible working-copy conversion are separate from this pipeline.
- See [Export and reproducibility](../EXPORTING.md) for auditing and supported scope

## Key Improvements

### For AI Collaboration
- **Smaller, focused files**: Each file ~30-1200 lines (manageable context)
- **Clear responsibilities**: Each module has a single purpose
- **Easier navigation**: Specific functionality easily located
- **Better testing**: Components can be tested independently

### For Code Maintenance
- **Separation of concerns**: UI logic, processing, and metadata handling separated
- **Reduced complexity**: Easier to understand individual components
- **Reusability**: Components like `FrameProcessor` can be imported and used elsewhere
- **Import clarity**: Dependencies are explicit

## Running the Application

Instead of:
```bash
python osvev.py
```

Use:
```bash
python main.py
```

## File Dependencies

```
main.py
  └── osvev_app.py
       ├── qt_compat.py
       ├── magnifier_label.py
       │    └── qt_compat.py
       ├── frame_processor.py
       ├── frame_info.py
       ├── playback_timing.py
       ├── export_dialog.py
       │    ├── ffmpeg_export.py
       │    └── export_runner.py
       └── pyav_input.py (default verified input only)
```

## Original File
The tracked application entry point is `main.py`. The ignored `old/` archive
may contain an earlier monolithic version; it is not imported by the application.

## Next Steps
- Replace `python osvev.py` with `python main.py` in any batch scripts or shortcuts
- Do not delete archived files or diagnostic media as part of baseline stabilization
- Run the regression suite with `python -m unittest discover -s tests -v`
- Use `verify_fidelity.py` for a read-only sampled frame-hash comparison
- Consult [Fidelity and recovered features](FIDELITY_AND_FEATURES.md) before restoring newer processing/export machinery
