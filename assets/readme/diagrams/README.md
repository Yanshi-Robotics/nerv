# NERV architecture figures

These original vector figures describe the NERV 0.8.2 candidate. English and Chinese
versions share geometry and terminology. They illustrate implemented responsibilities
and interfaces; they do not certify physical task performance.

| Figure | Purpose |
|---|---|
| `overview.svg` | Task setup, control schemes and execution, with shared observation and history |
| `architecture.svg` | Three responsibility layers, the fixed-policy bypass and shared controls |
| `control-schemes.svg` | The three supported templates and their different execution paths |
| `runtime.svg` | Host and real-device Runtime environments, device ownership and feedback records |

Regenerate the SVGs from the repository root with Python 3:

```bash
python3 assets/readme/diagrams/build.py
```

Edit wording and layout in `build.py`; regenerate both languages together. The output
uses native SVG text and shapes with no external fonts, scripts, images or CDN requests.
Text uses installed Noto Sans CJK SC, Arial or the system sans-serif fallback. The figures
have explicit light backgrounds so their contrast is retained in either GitHub theme.
Open the SVG at full size for detailed reading on narrow screens.

The restrained colour palette, fine connection lines and separated forward/feedback
paths follow the visual approach of Yanshi's Season 1 ACT courseware. The NERV layouts,
icons and captions are original to this repository; no course photographs or external
robot meshes are embedded. Sources and generated figures use the repository MIT licence.
