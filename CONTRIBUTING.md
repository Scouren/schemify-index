# Adding or updating a listing

Each package in the Schemify store is one file here, `packages/<name>`. You change it with a
pull request. Scouren reviews it, signs the index with an offline key and publishes the
signed index with a copy of every file it names at `store.scouren.com`. The Schemify app reads
only that signed index, so nothing you link can change after review.

## The short way

Install Schemify, then in your package folder:

```sh
schemify plugin check .                 # what this repository's checks will say
schemify plugin preview .               # your store page, as users will see it
gh release create v0.3.0 ...            # attach the files `publish` lists
schemify plugin publish . --index ~/src/schemify-index
```

`publish` writes `packages/<name>` for the version at the top of your `CHANGELOG.md`, keeps
your earlier releases below it, and opens the pull request with `gh` (or prints the steps).
The full guide is *Your store listing* in the Schemify plugin docs:
https://github.com/Scouren/Schemify-Plugins/blob/main/docs/plugins/publishing.md

## The file

`packages/<name>` is the package's block of the store index, exactly as the index holds it:
one `name value` line each, plain ASCII, in this order.

```
entry netcount
publisher acme
kind plugin
license MIT
categories docs
summary Logs the nets that have a single connection.
readme https://github.com/acme/netcount/releases/download/v0.3.0/README.md
icon https://github.com/acme/netcount/releases/download/v0.3.0/icon.png <sha256>
screenshot https://github.com/acme/netcount/releases/download/v0.3.0/screenshot.png <sha256> Count nets on an OTA
changelog https://github.com/acme/netcount/releases/download/v0.3.0/CHANGELOG.md
image https://github.com/acme/netcount/releases/download/v0.3.0/screenshot.png <sha256>
release 0.3.0 url https://github.com/acme/netcount/releases/download/v0.3.0/netcount.wasm <sha256>
asset 38211 2026-11-07
grant read_project Reads the open design to count its nets
tool netcount.count Count the nets with a single connection
release 0.2.0 url https://github.com/acme/netcount/releases/download/v0.2.0/netcount.wasm <sha256>
```

- The file is named after its `entry`. `publisher` is your GitHub org or user, lowercase.
- `categories`: one to four of `sizing simulation layout interop verification docs agent pdk`
  (plugins) or `dark light high-contrast vscode` (themes).
- Every URL is `https://`. Every file but the README and the changelog carries its sha256.
- Releases newest first, at most 8. A plugin release is its `.wasm` (`url`); a theme release
  is its repository at a commit (`git <url> <40-hex commit>`).
- `verified` and `installs` are set by Scouren; a pull request may not add them.

## Images

- PNG only. JPEG is refused.
- Icon: 256 × 256, at most 64 KiB. Banner: at most 512 KiB. Up to 6 screenshots with a
  one-line caption, at most 1 MiB each.
- README images: at most 16, each listed as an `image` line with its sha256, each alone on
  its line in the README (`![What it shows](screenshot.png)`, a path beside the README). Any
  other image, and any image from elsewhere, shows as *Image not shown in the store*.
- No raw HTML in the README; the store renders markdown only and never follows a link inside
  the app.

## Grants

Every capability your manifest requests needs a `grant` line with a reason users can judge:
what the plugin does with it (`Reads the cells it sizes`), not what the capability is. The
store shows the reasons in its permission dialog. An update that asks for a new grant is
installed only after each user allows it again.

## Review

The checks run on every pull request: the format, every URL fetched and its sha256, the image
rules and the grants against your previous release. Then a reviewer reads it, and Scouren
checks it again before publishing. These always get a manual review:

- a new publisher (the first listing from an org);
- a new grant over the previous release, and any risky grant: `spawn:*`, `network`,
  `write_project`, `read:*`, `commands`;
- a paid listing (seller onboarding first; see the plugin docs);
- a removed listing.

Releases listed before may not be edited, and the publisher may not change. No NDA content:
nothing from a foundry kit, model cards, rules or anything you may not share, in the package,
its README, its images or its changelog. A listing that breaks this is removed and its builds
revoked.

## When users see your update

Schemify never checks at startup. Users see **Update** on your package when they open the
store, or when they press *Check for updates* in the Packages pane, after Scouren publishes
the index with your release. Nothing installs on its own.

## The checker

`check/check.py` is the check the CI runs; `python3 check/check.py --base origin/main` runs it
locally on your branch (`--offline` skips the downloads). `check/fixtures` and their verdicts in
`check/fixtures/EXPECT` come from the Schemify app's own reader, and `check/test_check.py`
keeps the checker agreeing with it.
