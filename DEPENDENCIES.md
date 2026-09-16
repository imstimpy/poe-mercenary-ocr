# Dependencies

`capture_pipeline.py` needs three things installed:
 1. Python itself,
 2. Python packages (via pip)
 3. Tesseract OCR (a non-Python program)

** Missing the OCR engine is the single most common failure mode -- see 3. below **

## 1. Python

If you already have Python 3.12+ installed, you're set -- skip this
section.

Check with:
```powershell
python --version
```

If you don't have Python installed at all yet:
```powershell
winget install -e --id Python.Python.3.13
```
(`-e`/`--id` forces an exact package match rather than a fuzzy search --
worth using with winget generally, since ambiguous names can otherwise
resolve to something unexpected.)

## 2. Python packages

```
pip install -r requirements.txt
```

or individually:

```
pip install mss pillow keyboard pytesseract numpy scipy onnxruntime
```

| Package | What it's for |
|---|---|
| `mss` | Screen capture (grabs the frame on hotkey press) |
| `pillow` (`PIL`) | Image cropping/resizing/format conversion |
| `keyboard` | Global hotkey listener (F9 to capture, ESC to quit) |
| `pytesseract` | Python wrapper that calls the Tesseract engine -- **not** the engine itself, see below |
| `numpy` | Pixel array math in the OCR preprocessing (glyph isolation) |
| `scipy` | Connected-component analysis (`scipy.ndimage`) used to separate real text glyphs from decorative UI elements and icons before OCR |
| `onnxruntime` | Runs the gem-presence embedding model (`assets/models/gem_embedding_resnet18.onnx`) -- inference only, no training/export capability |

`numpy` 2.5.x and `scipy` 1.18.x (the versions `requirements.txt` pulls)
both publish prebuilt wheels for Python up to 3.14 on Windows -- any recent 3.12+
install works.

### Regenerating the gem-presence model (not needed for normal use)

`assets/models/gem_embedding_resnet18.onnx` is already built and
checked in -- most people never need to touch this. It only needs
regenerating if `assets/gems/` (the reference gem icons) changes.
Doing that needs a much heavier, separate set of packages
(`requirements-dev.txt`: `torch`, `torchvision`, `onnx`, `onnxscript` --
hundreds of MB, versus `onnxruntime`'s ~46MB):

```
pip install -r requirements-dev.txt
cd tools
python export_gem_embedding_model.py
```

See `tools/README.md` for what it reads/writes.

## 3. Tesseract OCR engine (NOT a pip package)

**This is the one that's easy to miss.** `pip install pytesseract` only
installs a thin Python wrapper that calls a separate compiled
program called Tesseract. pip cannot install Tesseract. If `pytesseract`
throws `TesseractNotFoundError`, this is almost always why, even after
`pip install`-ing everything above.

**Windows install:**
1. Download the installer from the UB Mannheim build (the standard
   Windows distribution, since the Tesseract project itself doesn't
   publish one directly): https://github.com/UB-Mannheim/tesseract/wiki
2. Run it. Accept the default install path
   (`C:\Program Files\Tesseract-OCR`) unless you have a specific reason
   not to -- the default makes the next step predictable.
3. Verify it actually landed there:
   ```powershell
   Test-Path "C:\Program Files\Tesseract-OCR\tesseract.exe"
   ```
   Should return `True`. If it doesn't, check
   `C:\Program Files (x86)\Tesseract-OCR\tesseract.exe` instead (32-bit
   path), or search the whole drive:
   ```powershell
   Get-ChildItem -Path C:\ -Filter tesseract.exe -Recurse -ErrorAction SilentlyContinue -File
   ```
4. Point the script at it directly rather than relying on PATH (PATH
   updates from an installer don't always propagate to an already-open
   terminal, and sometimes need a full reboot, not just a new terminal
   window) -- in `capture_pipeline.py`:
   ```python
   TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
   ```

**macOS (Untested):** `brew install tesseract`

**Linux (Untested):** `sudo apt install tesseract-ocr` (Debian/Ubuntu) or your
distro's equivalent package name (`tesseract` or `tesseract-ocr`).

## 3. Verifying everything is actually wired up

Run these in order -- each one isolates a different layer, so whichever
one fails tells you exactly where the problem is:

```powershell
# 1. Is Tesseract itself installed and runnable at all?
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --version

# 2. Can Python's pytesseract wrapper find and call it?
python -c "import pytesseract; pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'; print(pytesseract.get_tesseract_version())"

# 3. Does the full script import cleanly (catches missing pip packages)?
python -c "import capture_pipeline"
```

If (1) fails: Tesseract isn't installed correctly -- reinstall.
If (1) works but (2) fails: `TESSERACT_CMD` in the script (or your PATH)
points somewhere wrong -- double check the exact path.
If (2) works but (3) fails: a pip package is missing -- re-run
`pip install -r requirements.txt` and read the `ModuleNotFoundError` for
which one.

## Known quirks

- **`mss.mss()` deprecation warning**: fixed in the script to use `mss.MSS()`.
- **`pip install tesseract` does *not* install the OCR engine.** There
  is no legitimate pip package that installs the compiled Tesseract
  binary for you. Use the standalone installer above.
- **Windows PATH changes need a fresh terminal *or* reboot** -- and
  sometimes a fresh terminal alone isn't enough, since PATH is
  inherited at process-launch time from Explorer/the shell that spawned
  it. Setting `TESSERACT_CMD` explicitly in the script sidesteps this
  entirely, which is why that's the recommended approach here rather
  than fighting PATH.
