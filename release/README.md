# Candidate distribution

`constraints/` fixes third-party versions separately for the host, LeRobot and simulation.
The build resolves platform markers and creates hashed install locks. NERV wheel hashes
come from the current build, so rebuilding a candidate cannot reuse stale wheel hashes.

Run `python scripts/build_release.py` from the repository. It produces an extracted
bundle and `nerv-VERSION-bundle.zip` under `temp/release`. No publishing is performed.
Students can use the extracted bundle's Shell/PowerShell installer. Keep hardware and
student acceptance separate from CI and package checks.
