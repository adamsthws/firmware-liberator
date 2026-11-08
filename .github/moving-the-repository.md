# Moving the repository

For the repository's owner: what to change when the repository moves to an
organisation or another account, so nothing is left naming the old one.

Use GitHub's transfer, which takes the issues, discussions and releases with
it. GitHub then redirects every old address - pages, `git`, release
downloads - for as long as no repository takes the old name. So keep the
account, and never create a `firmware-liberator` under it again: published
images and one-line installs depend on that redirect.

Then, in one commit:

- **`REPOSITORY` in `tools/repo/github_settings.py`**, the address written
  once. Its copies must agree with it, and `tests/repo/test_repository_address.py`
  fails on GitHub until they do: `public_plugs.py`, `private_backup.py`,
  `website/src/site.mjs`, and each device's `esphome.yaml`, whose
  `package_import_url` is where Home Assistant fetches the config from.
  Images already published keep the old address, through the redirect.
- **The one-line installs**: `REPO` in `liberate.sh` and `liberate.ps1`, the
  URLs in their comments, and the two in `website/public/_redirects`.
- **The tool's links**: `REPORTS_URL` in `tools/autoflash/report.py`,
  `DISCUSSIONS_URL` and `ISSUES_URL` in `tools/autoflash/runner.py`.
- **The emulator images**: `PUBLISHED` and the release download URL in both
  `tools/espressif/emulator/image.sh` and `tools/beken/emulator/image.sh`.
  Packages on ghcr.io stay with the account that published them and are not
  redirected; the image workflows publish under the new owner by themselves
  (`GITHUB_REPOSITORY_OWNER`), so run them once.
- **Links in prose**: `git grep` the old address in `README.md`, `docs/`,
  `devices/`, `.github/` (the issue and pull request templates), `SECURITY.md`,
  `CONTRIBUTING.md` and `AGENTS.md` (its `gh -R`).

Left as they are: `EARLIER_ESPHOME_NAMESPACES` in `tools/autoflash/kinds.py`,
which only names the old images; the sponsorship links (`.github/FUNDING.yml`,
`report.py`), which are a person's, not the repository's; the authors in the
emulator's patches; and the private backup's package
(`OWNER` in `tools/repo/private_backup.py`), which may stay where it is: move
it only by uploading it again under the new owner, private
([private-firmware-backup.md](private-firmware-backup.md)).

After the push, `python3 tools/repo/github_settings.py` says whether every
setting came across, the `website` environment's secrets included, and
`... apply` sets what did not (`... secret NAME` a secret). Check that a release
still verifies (`gh release verify`).
