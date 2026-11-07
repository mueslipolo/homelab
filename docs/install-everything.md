# Install everything

The whole homelab on an empty Synology, in dependency order. Each step is a
guide; this page only gives the order and what ties them together. Rebuilding
after a loss follows the same order, restoring each stack's data before
deploying it ([operations: restore](operations.md#restore)).

Only want one part? Start at its guide: the header lists what it needs. What
is required and what you can change: [adapting](adapting.md).

| #   | Guide                                                                                                                                                                                                                           | Needed?                                                      | Gives the next steps                                                                | Check                                                                      |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ | ----------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| 1   | [01 Synology prep](guides/01-synology-prep.md)                                                                                                                                                                                  | yes (LDAP Server optional)                                   | Container Manager, SSH, shared folders, the `proxy` network; optionally LDAP Server | `docker network ls` shows `proxy`                                          |
| 2   | [02 Dockhand](guides/02-dockhand.md), steps 1–3                                                                                                                                                                                 | optional: `docker compose` works too                         | the tool that deploys every stack                                                   | `http://<nas-ip>:3010` asks for a login                                    |
| 3   | [04 Forgejo](guides/04-forgejo-gitops.md), first part                                                                                                                                                                           | optional: any git host, or none                              | the git server every git stack pulls from, and a token for Dockhand                 | the repo's files at `http://<nas-ip>:3000`                                 |
| 4   | [03 HTTPS](guides/03-https-letsencrypt.md)                                                                                                                                                                                      | yes                                                          | `https://<app>.<internal-domain>` with a valid certificate                          | `https://dockhand.<internal-domain>` ([02](guides/02-dockhand.md), step 7) |
| 5   | [05 Authelia](guides/05-sso-authelia.md)                                                                                                                                                                                        | optional, except for Mealie                                  | one login for the apps; OIDC for Dockhand and Forgejo                               | a login at `https://auth.<internal-domain>`                                |
| 6   | apps, any order: [06 mediarr](guides/06-mediarr.md), [07 Paperless](guides/07-paperless.md) (then paperless-tarmed), [08 Mealie](guides/08-mealie.md), [09 dashboard](guides/09-dashboard.md), [12 Immich](guides/12-immich.md) | the ones you want                                            | the services                                                                        | each guide's last check                                                    |
| 7   | [04 Forgejo](guides/04-forgejo-gitops.md), CI part                                                                                                                                                                              | optional                                                     | checks on every push, Renovate's update PRs                                         | a push runs `checks` green                                                 |
| 8   | [10 Backups and jobs](guides/10-backups-and-jobs.md)                                                                                                                                                                            | backups: yes; the scheduler: for app dumps and the dashboard | Hyper Backup, snapshots, the scheduler                                              | the dashboard's left panel is green                                        |
| 9   | [11 Notifications](guides/11-notifications.md)                                                                                                                                                                                  | optional                                                     | ntfy, and the apps and DSM sending to it                                            | a test message on your phone                                               |

Why this order:

- **Forgejo before HTTPS:** Dockhand clones git stacks from Forgejo's raw IP
  (`http://<nas-ip>:3000`), which works before Caddy and DNS do. `edge` is the
  first stack deployed from it. Until Caddy serves Dockhand, its port is on
  the LAN, with its login on ([02](guides/02-dockhand.md), steps 2 and 7).
- **Authelia after HTTPS:** its portal and OIDC issuer are
  `https://auth.<internal-domain>`.
- **Apps after Authelia:** their Caddy blocks use `import authelia`, or they
  are OIDC clients. Without Authelia, see the "without Authelia" notes in guides 06, 07 and 09;
  Mealie (08) needs Authelia.
- **Paperless before paperless-tarmed:** the add-on calls Paperless's API.

Last, on the workstation you edit this repo from:
[operations: checks](operations.md#checks) (pre-commit and
`.private-patterns`).
