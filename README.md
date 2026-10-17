# git-credential-github-app

A small Bash Git credential helper and optional `gh` wrapper for GitHub App
installations. No daemon or Python runtime dependencies.

```text
Private key → signed JWT → GitHub installation token → HTTPS Git
```

By default, Git and `gh` share a cached token with all repositories and
permissions granted to the app installation. GitHub enforces that access. Works with any automation that uses Git, including AI agents.

## Requirements

- Bash 3.2+, Git, jq, OpenSSL, curl 7.55+ (macOS or Linux)
- A GitHub App installed on selected repositories with Contents read/write
  (or read-only for fetch-only use)
- Its private key, App ID or Client ID, and numeric Installation ID

GitHub.com and HTTPS remotes only; no SSH or GitHub Enterprise Server support.
See GitHub's [registration guide](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app).

## Install

From this project's directory:

```bash
# macOS, if jq is missing:
brew install jq

mkdir -p "$HOME/.local/bin" "$HOME/.config/git-credential-github-app"
chmod 700 "$HOME/.config/git-credential-github-app"
install -m 755 git-credential-github-app "$HOME/.local/bin/git-credential-github-app"

# Substitute your downloaded key filename:
install -m 600 "$HOME/Downloads/YOUR-APP.private-key.pem" \
  "$HOME/.config/git-credential-github-app/app.pem"
```

There is no standalone configuration file. Pass settings as flags in Git's
credential helper configuration. Keep the private key outside this project.

| Flag | Value |
| --- | --- |
| `--app-id` | App ID or Client ID (required) |
| `--installation-id` | Numeric installation ID (required) |
| `--owner` | GitHub user or organization owning the repositories (required) |
| `--key-file` | Absolute path or `~/` path to the private key (required) |
| `--contents-permission` | Optional: restrict to Contents `read` or `write`; Git mode also restricts to the requested repository |
| `--cache-dir` | Absolute cache directory; defaults to `${XDG_CACHE_HOME:-$HOME/.cache}/git-credential-github-app` |

Put flags before the operation (`get`, `store`, or `erase`), which Git appends
automatically. IDs and key paths are not secrets; private key contents never
go into Git config or command arguments.

`jq` is still used to build JWT claims/API request JSON and parse GitHub's
installation-owner/token responses. It is not needed for local configuration.

## Global setup for one GitHub owner

`--global` writes to your user-level Git configuration (normally `~/.gitconfig`)
and applies across checkouts. `--local` writes only to the current checkout's
`.git/config`. Choose this global setup or the per-checkout setup below.

The owner is the user or organization in `https://github.com/OWNER/REPOSITORY`,
not the app's name. Use the account where you installed the app. The following
configuration selects the helper for HTTPS repositories under that owner;
GitHub still limits access to the installation's selected repositories.

Run from any directory, replacing the owner, IDs, helper path, and key path:

```bash
git config --global credential.https://github.com.useHttpPath true

git config --global --replace-all \
  'credential.https://github.com/YOUR_OWNER.helper' ''

git config --global --add \
  'credential.https://github.com/YOUR_OWNER.helper' \
  '!"/absolute/path/to/git-credential-github-app/git-credential-github-app" --app-id 123456 --installation-id 98765432 --owner YOUR_OWNER --key-file "/absolute/path/to/app.pem"'
```

The helper can run directly from any checkout; installing into `~/.local/bin`
is optional. Use the absolute path to the helper in your Git configuration.
Keep it executable (`chmod +x /absolute/path/to/helper`) and keep the private key
outside the repository. If you move the helper, update Git config accordingly.

Other owners retain their existing authentication. SSH remotes still use SSH
keys. With this global setup, ordinary HTTPS clones need no extra helper flags:

```bash
git clone https://github.com/YOUR_OWNER/YOUR_REPO.git
# Read-only authentication check for an existing repository:
git ls-remote https://github.com/YOUR_OWNER/YOUR_REPO.git HEAD
```

If you previously applied this README's local setup, run the following inside
each affected checkout to remove those local overrides:

```bash
git config --local --unset-all credential.helper
git config --local --unset-all credential.useHttpPath
```

These commands return a nonzero status if the setting is already absent. They
remove all local values for those two keys; retain them if deliberately used for
another setup. URL-specific local helpers can also affect authentication. Inspect
credential settings and where they were defined with:

```bash
git config --show-origin --get-regexp '^credential\.'
```

## Configure an existing checkout

Run inside the checkout you want to authenticate as the app:

```bash
git remote set-url origin https://github.com/YOUR_OWNER/YOUR_REPO.git
git config --local --replace-all credential.helper ''
git config --local --add credential.helper \
  '!"$HOME/.local/bin/git-credential-github-app" --app-id 123456 --installation-id 98765432 --owner YOUR_OWNER --key-file "$HOME/.config/git-credential-github-app/app.pem"'
git config --local credential.useHttpPath true

# Read-only verification:
git ls-remote origin HEAD
```

The empty helper entry clears inherited helpers for this checkout. The explicit
path works without changing PATH. Existing URL-specific helpers, `http.extraHeader`,
URL rewrites, and `remote.origin.pushurl` may also affect authentication; inspect
those if this checkout previously had custom routing.

## Clone a new checkout

```bash
git clone \
  --config credential.helper= \
  --config credential.helper='!"$HOME/.local/bin/git-credential-github-app" --app-id 123456 --installation-id 98765432 --owner YOUR_OWNER --key-file "$HOME/.config/git-credential-github-app/app.pem"' \
  --config credential.useHttpPath=true \
  https://github.com/YOUR_OWNER/YOUR_REPO.git
```

Replace the example IDs and owner before running these commands. These settings
persist in the new checkout. Subsequent `git fetch`, `pull`, and `push` use the
helper automatically. Use different flag values in another checkout for another
installation. Add `--contents-permission read` for fetch-only credentials.

## GitHub CLI and agent setup

The optional `bin/gh` wrapper uses the same JWT/token exchange and cache as Git.
Both send an empty token request (`{}`) by default, inheriting all repositories
and permissions granted to the installation. Manage access in GitHub; no local
permission list is required. Explicit `--contents-permission` restrictions use
separate cache entries. In `token` mode that option restricts permissions only;
in Git mode it also restricts access to the requested repository.

Keep the checkout in place and configure the environment of the shell or agent
that runs `gh` (replace all example values):

```bash
export GH_APP_ID=123456
export GH_APP_INSTALLATION_ID=98765432
export GH_APP_OWNER=YOUR_OWNER
export GH_APP_KEY_FILE="/absolute/path/to/app.pem"
export GH_APP_GH_BIN="/absolute/path/to/real/gh"
export PATH="/absolute/path/to/git-credential-github-app/bin:$PATH"
gh pr list --repo YOUR_OWNER/YOUR_REPO
```

Find the real binary with `command -v gh` **before** changing PATH (Homebrew on
Apple Silicon typically installs it at `/opt/homebrew/bin/gh`). The wrapper
passes arguments, output, and exit status through, and replaces any inherited
`GH_TOKEN` for its child process. No personal `gh auth login` is required.
For agents, make these variables and PATH available to the command execution
environment. Tell the agent authentication is automatic. For zsh login sessions,
place exports in `~/.zprofile` after any Homebrew setup; non-login execution
needs an inherited environment or its own startup configuration.

Tokens are cached under `${XDG_CACHE_HOME:-$HOME/.cache}/git-credential-github-app`
and renewed when fewer than 60 seconds remain. Directory/file permissions are
700/600; writes are atomic. Cache entries separate app IDs, installation IDs,
owners, token request bodies, and public-key fingerprints. Git and `gh` reuse
the same entry when those inputs match. Concurrent cold starts may
mint more than one token; subsequent calls reuse the cached token.
Delete the corresponding cache file to refresh early after revocation or changed
installation access. Failed commands are not automatically retried, to avoid
repeating writes. A running `gh` process does not refresh its token mid-command.
Commands requiring permissions not granted to the installation, or requiring
user authentication, remain unavailable. After changing permissions, clear the
cache to obtain a token with the new grants.

For other integrations, the helper also supports `token` with the same identity
flags and optional `--cache-dir /absolute/path`. It prints the raw cached token
to stdout: capture it rather than displaying it in agent logs. The `token`
operation does not read Git credential input. The existing Git helper install
command alone does not install the optional wrapper; run it from the checkout.

## Commit identity is separate

This helper changes authentication only. If desired, configure a verified email
alias on your GitHub account to label agent-authored commits:

```bash
git config --local user.name 'Your Name (AI agent)'
git config --local user.email 'your-verified-agent-alias@example.com'
```

## Behavior and limitations

- `get`: validates HTTPS host, repository path, arguments, and installation owner;
  returns a cached token, or signs an RS256 JWT and requests a fresh token.
  Installation owner is checked when minting; cached tokens reuse that check.
- `store`: no-op. `erase`: removes the matching cache entry without a network
  request. With default settings this also invalidates the shared `gh` cache;
  it does not revoke tokens already held by other processes.
- Unrelated hosts/owners: returns no credentials, allowing another helper to run.
- Failures for the configured owner: emits `quit=true` to prevent falling back
  to personal credentials. Errors go to stderr, not the credential stream.
- Installation tokens expire after one hour; cached tokens are reused until
  60 seconds before expiry. Existing restricted caches use different keys from
  the new default and are not reused.
- Protect the private key like an SSH key. Processes that can read it can request
  tokens within the app's full granted access; this helper is not a sandbox.
  Avoid shell tracing and credential-debug output.
- The optional wrapper supplies `gh` authentication; Git mode does not. Neither
  mode configures commit signing, branch-protection bypass, or keychain storage.

## Publishing this project

The helper can push to an existing repository once the app has access. Creating
a repository through the app is a separate GitHub API operation requiring
appropriate installation permissions. This helper does not create repositories.
Choose the owner/visibility and grant creation permissions separately, or create
the repository in GitHub's UI and select it in the app installation.

A license and release version should be chosen before public distribution.

## Tests

```bash
bash -n git-credential-github-app
bash -n bin/gh
python3 -B -m unittest discover -s tests -v
```

Tests use temporary RSA keys, real OpenSSL signing, fake HTTP responses, and a
local Git repository. No real credentials or network access are required.

## References

- [Official Bash JWT example](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app)
- [Installation token exchange](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)
- [Git credential helper protocol](https://git-scm.com/docs/gitcredentials)
