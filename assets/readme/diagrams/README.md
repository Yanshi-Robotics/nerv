# NERV architecture figures

The current figures describe the NERV 0.9.0 local candidate. English and Chinese share
geometry, use editable SVG text and shapes, and distinguish current interfaces from
future deployment work. They are architecture illustrations, not screenshots or test evidence.

| Figure | Purpose |
|---|---|
| `platform-090.svg` | External decisions, operator authority, Host services and Runtime execution |
| `supervision-090.svg` | Observation delivery, acknowledgement, execution and local stop expiry |
| `lifecycle-090.svg` | Project, current/historical Run, Task and Execution lifetimes |
| `deployments-090.svg` | Same-machine MCP and separately marked future Brain/cloud deployment |

Regenerate these figures from the repository root:

```bash
python3 assets/readme/diagrams/build_090.py
```

`build_090.py` contains four independently composed layouts and reuses drawing primitives
from `build.py`. Each generated SVG includes design notes, an accessible title and description.
The figures have opaque light backgrounds for GitHub theme compatibility, require no external
fonts or network assets, and use the repository MIT license. Render both languages when editing;
check labels, path crossings and the distinction between implemented and future capabilities.

## Earlier figures

The original `overview.svg`, `architecture.svg`, `control-schemes.svg` and `runtime.svg`
remain 0.8-era references. Regenerate those with `python3 assets/readme/diagrams/build.py`.
They describe the earlier internal conversation product and must not be used as the current
0.9 Agent contract. Existing photographs, scene captures and demo videos remain separately
version-labelled under `assets/readme/images` and `assets/readme/media`.
