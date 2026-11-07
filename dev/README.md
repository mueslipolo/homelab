# dev

For this repo itself, not the homelab: the checks every commit passes, publishing
to GitHub, the docs' screenshots.

| Script                          | Does                                                                                                                                                                        |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `check-no-real-values.sh`       | blocks a commit that would put this deployment's real values (domain, NAS paths, names, UIDs) into tracked files; run by pre-commit and CI, with your `.private-patterns`   |
| `dashboard-demo/screenshots.sh` | regenerates `docs/images/dashboard-*.png`: the dashboard with made-up data, in Playwright's Docker image                                                                    |
| `publish-github.sh`             | publishes Forgejo's `main` to the public GitHub copy as one snapshot commit (no bot commits, no history); checks real values and secrets first. `-n` shows what it would do |
