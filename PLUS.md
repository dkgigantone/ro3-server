# RO3Emu Plus

This repository is a fork of [aethai/ro3emu-server](https://github.com/aethai/ro3emu-server). It carries the
upstream server plus extra fixes and custom content that upstream has not merged (yet).

All credit for the server goes to the upstream project and its contributors. This repository stays under the same
license as upstream, the [GNU General Public License v3.0](LICENSE); the `LICENSE` file and the copyright notices in the
source files are kept as they are. Ragnarok Online is a trademark of Gravity Co., Ltd. No game assets are stored here.

## Branches

- `main` is upstream's `main` plus the fixes merged here. It is updated from upstream with
  `git fetch upstream` and `git merge upstream/main`.
- `fix/<short-name>`: one branch per fix, opened as a pull request into this repository's `main`.
- `custom-*`: custom content (for example the custom hat tools). Kept off `main` until it is ready.

## Workflow for every change

1. Open an issue here that describes the behavior, the expected behavior and how to reproduce it.
2. Make the fix on its own branch, following upstream's `AGENTS.md` and `CONTRIBUTING.md` (warnings are errors,
   format only the changed files, tests, `(inferred)` marks, update the docs).
3. Open a pull request into this repository's `main` that says `Closes #<issue>`, with the sections
   What changes / Evidence and limits / Validation.
4. After the merge, add a line to [FIXES.md](FIXES.md) with the issue and the pull request.
5. If the fix is useful to everyone, the same branch can also be offered to upstream as a pull request.

## Updating from upstream

```
git fetch upstream
git checkout main
git merge upstream/main
git push plus main
```

Conflicts, if any, are most likely in files that both upstream and our fixes touched. Keep both sides, build and run
the tests before pushing.
