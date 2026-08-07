# SLSA Nodel host snapshot

This repository is a flat snapshot of the nodes running across the four SLSA
Nodel hosts. Each host runs from `C:\Nodel`, with its nodes under
`C:\Nodel\nodes`.

## Node ownership

| Host | Nodes |
| --- | --- |
| `LTL-NODEHOST` | `LTL-LIB-AUDIO`, `LTL-LIB-DASHBOARD`, `LTL-LIB-DYNALITE`, `LTL-LIB-GALLERY`, `LTL-LIB-LIGHTING-POWER`, `LTL-LIB-MEDIA-SCHEDULE`, `LTL-LIB-PLAY`, `LTL-LIB-SCHEDULER`, `LTL-LIB-SING`, `LTL-PLAY-AMP`, `LTL-PLAY-INT-WOL`, `LTL-PLAY-PJ`, `LTL-PLAY-PJPDU`, `LTL-PLAY-SCAN01-WOL`, `LTL-PLAY-SCAN02-WOL`, `LTL-PLAY-SCANNER01`, `LTL-PLAY-SCANNER02`, `LTL-PLAY-TABLEPDU01`, `LTL-PLAY-TABLEPDU02`, `LTL-SING-AMP`, `LTL-SING-PC-WOL`, `LTL-SING-PJ`, `LTL-SING-RACKPDU01`, `LTL-SING-RACKPDU02`, `LTL-SING-SWITCHER` |
| `LTL-SING-PC` | `LTL-SING-PC`, `xxMPV-eng` |
| `LTL-PLAY-SCAN01` | `LTL-PLAY-BOT01`, `LTL-PLAY-SCAN01`, `LTL-PLAY-SCANMON01`, `LTL-PLAY-SNAP01` |
| `LTL-PLAY-INT` | `LTL-PLAY-INT`, `LTL-PLAY-UNITY` |

The automatic Recipes Sync node exists on each exhibit PC but is represented
once by the variable-based `Nodel Recipes Sync for $HOSTNAME ...` directory.

## Capture boundary

`LTL-NODEHOST`, `LTL-SING-PC`, and `LTL-PLAY-SCAN01` are captured read-only over
SSH. `LTL-PLAY-INT` is captured through the Nodel REST API: its advertised files
are downloaded and `nodeConfig.json` is checked against the live parameters and
remote bindings. The API does not expose that host's generated `.nodel` runtime
directory.

All four hosts currently report Nodel 2.2.1 on Java 11 and Windows 11, using the
default node root and an include-all hosting rule. The top-level `nodel.jar` and
`.version` belong to `LTL-NODEHOST`; exhibit-PC binaries are not merged here.

Snapshots preserve existing tracked `.nodel` JSON state but exclude newly
generated `.nodel` trees, compiled classes, executable process binaries, logs,
caches, virtual environments, session transcripts, new backup files, host
deployments, recipe checkouts, and machine-access credentials. Existing tracked
backup files are retained to avoid unrelated cleanup.
