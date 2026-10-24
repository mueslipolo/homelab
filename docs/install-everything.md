# Install everything

The whole homelab on an empty Synology, in dependency order. Each step is a
guide; this page only gives the order and what ties them together. Rebuilding
after a loss follows the same order, restoring each stack's data before
deploying it ([operations: restore](operations.md#restore)).

Only want one part? Start at its guide: the header lists what it needs.

| #   | Guide                                                                                                                                                                                         | Gives the next steps                                                        | Check                                             |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------- |
| 1   | [01 Synology prep](guides/01-synology-prep.md)                                                                                                                                                | Container Manager, SSH, shared folders, LDAP Server, the `proxy` network    | `docker network ls` shows `proxy`                 |
| 2   | [02 Portainer](guides/02-portainer.md)                                                                                                                                                        | the tool that deploys every stack                                           | `https://<nas-ip>:9443` logs in                   |
| 3   | [04 Forgejo](guides/04-forgejo-gitops.md), first part                                                                                                                                         | the git server every Repository stack pulls from, and a token for Portainer | the repo's files at `http://<nas-ip>:3000`        |
| 4   | [03 HTTPS](guides/03-https-letsencrypt.md)                                                                                                                                                    | `https://<app>.<internal-domain>` with a valid certificate                  | `https://portainer.<internal-domain>`             |
| 5   | [05 Authelia](guides/05-sso-authelia.md)                                                                                                                                                      | one login for the apps; OIDC for Portainer and Forgejo                      | an LDAP login at `https://auth.<internal-domain>` |
| 6   | apps, any order: [06 mediarr](guides/06-mediarr.md), [07 Paperless](guides/07-paperless.md) (then paperless-tarmed), [08 Mealie](guides/08-mealie.md), [09 dashboard](guides/09-dashboard.md) | the services                                                                | each guide's last check                           |
| 7   | [04 Forgejo](guides/04-forgejo-gitops.md), CI part                                                                                                                                            | checks on every push, Renovate's update PRs                                 | a push runs `checks` green                        |
| 8   | [10 Backups and jobs](guides/10-backups-and-jobs.md)                                                                                                                                          | Hyper Backup, snapshots, the scheduler                                      | the dashboard's left panel is green               |
| 9   | [11 Notifications](guides/11-notifications.md)                                                                                                                                                | ntfy, and the apps and DSM sending to it                                    | a test message on your phone                      |

Why this order:

- **Forgejo before HTTPS:** Portainer clones Repository stacks from Forgejo's
  raw IP (`http://<nas-ip>:3000`), which works before Caddy and DNS do. `edge`
  is the first stack deployed from it.
- **Authelia after HTTPS:** its portal and OIDC issuer are
  `https://auth.<internal-domain>`.
- **Apps after Authelia:** their Caddy blocks use `import authelia`, or they
  are OIDC clients. Without Authelia, see the "without Authelia" notes in guides 06, 07 and 09;
  Mealie (08) needs Authelia.
- **Paperless before paperless-tarmed:** the add-on calls Paperless's API.

Last, on the workstation you edit this repo from:
[operations: checks](operations.md#checks) (pre-commit and
`.private-patterns`).
