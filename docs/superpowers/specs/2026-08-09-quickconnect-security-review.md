# Security Review: Synology QuickConnect

**Status (2026-10-11): research only, no decision.** Exposing Caddy directly stays rejected (it would add an unauthenticated public path); QuickConnect and Cloudflare Tunnel are both still open. Caddy remains LAN-only until this is decided.

Context: evaluating whether to use QuickConnect to expose a single Synology Photos shared album to extended family (planned in the local-only backup notes, kept outside this repo). Current setup serves everything LAN-only through a self-managed Caddy reverse proxy (own macvlan LAN IP, nothing port-forwarded) with publicly trusted Infomaniak DNS-01 certs (`docs/ARCHITECTURE.md`), not QuickConnect. This review evaluates QuickConnect on its own merits against that baseline and against Cloudflare Tunnel, without picking a winner.

## 1. How QuickConnect actually works

Three-tier connection fallback, per Synology's own White Paper:

1. **LAN/WAN detection** — client asks the QuickConnect server for the NAS's registered IPs and NAT type, tries a direct connection first.
2. **Hole punching (P2P)** — if direct fails, NAS and client each open a NAT port, Synology's server brokers a direct virtual tunnel. Traffic doesn't touch Synology's infrastructure beyond the handshake.
3. **Relay fallback** — if hole punching fails (common behind CGNAT/restrictive NAT, plausible for a home ISP), **all traffic routes through a Synology Relay Server** — described as the "final option."

**The encryption claim doesn't hold up under the relay path.** Marketing describes QuickConnect as end-to-end encrypted. Synology's own White Paper contradicts this for relay mode: a component called the **Portal Server** "offers a trusted certificate for the connecting browser," then **"decrypt[s] and modif[ies] the specific HTTP headers"** before re-encrypting and forwarding to the NAS. This is a TLS-terminating reverse proxy, not a blind relay — there's a real plaintext window on Synology's infrastructure, for exactly the HTTPS traffic a Photos share link would use. (HIGH confidence — primary source, Synology's own White Paper.)

**What Synology's servers see/log**: per Synology's Services Data Collection Disclosure, QuickConnect requires a Synology Account; Synology collects device serial number, IP address, and routing ports to identify/route connections. Synology states staff don't have routine DB access outside support cases — this is a vendor assertion, not independently audited (MEDIUM confidence).

## 2. Known security incidents / CVEs specific to QuickConnect

- **CVE-2022-45188** (Claroty, Pwn2Own Toronto 2022) — **demonstrated on a DS920+, the exact model this NAS is.** Abused weak device authentication (derivable from serial number, not a secret) to impersonate a NAS to QuickConnect's cloud and redirect a real user's connection to an attacker's host. Root cause was architectural (trust based on public information), not a simple bug. Patched (Synology-SA-22:23). HIGH confidence.
- **CVE-2021-26566** — CVSS 9.0, arbitrary command execution via the relay daemon (`synorelayd`) itself. Patched DSM 6.2.3-25426-3 (2021). HIGH confidence.
- **Stale-DNS relay interception** (Full Disclosure, July 2026) — ephemeral relay IPs get reassigned faster than some clients' DNS caches expire; a researcher captured reassigned IPs and harvested auth keys/tokens from older DSM clients. Not applicable to DSM 7.3.2, but shows a repeat structural pattern in the relay's trust model over 5+ years. MEDIUM-HIGH confidence (single detailed disclosure, not yet independently cross-confirmed).
- **CVE-2020-27653 / CVE-2020-27655** — same "QuickConnect" brand, different product (Synology Router Manager, not DSM). Not directly applicable, but shows the trust model recurs across product lines. HIGH confidence.
- **CVE-2024-10443 / "RISK:STATION"** (Midnight Blue, Pwn2Own Ireland 2024) — not a QuickConnect flaw itself, but QuickConnect is the exposure vector for a zero-click RCE in Synology Photos/BeePhotos, the exact app relevant here. Key finding: Synology auto-provisions a public Let's Encrypt cert per device, permanently logged in public Certificate Transparency logs — used to enumerate ~1-2 million exposed Photos/BeePhotos instances. **Enabling QuickConnect makes the NAS discoverable regardless of whether a link is ever shared.** HIGH confidence.

No documented case found of plain credential-stuffing against DSM logins with QuickConnect as merely the transport — that risk exists generically for any internet-facing login, not something QuickConnect-specific was found in this research.

## 3. Is exposure scoped to just one app?

Partially. Control Panel → External Access → QuickConnect → Advanced has a per-application permission list — DSM itself can be unchecked, leaving only e.g. Photos reachable (MEDIUM confidence, KB page didn't render for direct primary-source quote, cross-referenced via multiple how-to guides).

**Not scoped by this setting**: the QuickConnect ID/hostname itself becomes publicly enumerable via CT logs the moment QuickConnect is enabled at all, independent of which apps are ticked. Unchecking DSM stops it from _responding_ over that path; it doesn't un-expose the NAS's existence.

Unverified (LOW-MEDIUM confidence): whether unchecking DSM blocks access only via QuickConnect's own routing, or whether ports 5000/5001 remain separately reachable if the router also has UPnP/port-forwarding active — a router-level concern independent of QuickConnect, worth checking directly rather than assuming.

## 4. Synology's own hardening recommendations

- 2FA (Secure SignIn app or TOTP), enforceable org-wide.
- DSM 7 auto-disables the built-in `admin` account once a named admin is created during setup.
- Auto Block (lock out an IP after N failed logins) — general DSM recommendation, more relevant once anything is internet-reachable.
- HTTPS redirect for QuickConnect's Smart DNS — doesn't change the relay TLS-termination finding above, only secures the client-to-portal leg.

No Synology security advisory found that flags the relay's TLS-termination behavior as a risk — that framing came only from the White Paper's own technical description and independent research, not Synology's own security guidance.

## 5. Comparative risk framing

|                                   | QuickConnect (relay mode)                                                         | Self-managed Caddy + real WAN IP (current)                            | Cloudflare Tunnel                                                                                                                                                                                                                                 |
| --------------------------------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Who terminates TLS                | Synology's Portal Server, then re-encrypts to NAS                                 | You — every key stays yours                                           | Cloudflare's edge — architecturally similar exposure to QuickConnect, different vendor with a larger public security program                                                                                                                      |
| Plaintext exposure to third party | Yes, momentarily, on Synology's infra                                             | None                                                                  | Yes, at Cloudflare's edge                                                                                                                                                                                                                         |
| Discoverability                   | Enumerable via CT logs regardless of link sharing                                 | Discoverable if DNS is public, but you control what's actually served | Not tied to a per-device cert/CT-log pattern the way QuickConnect is                                                                                                                                                                              |
| New infrastructure required       | None — built into DSM                                                             | None — already running                                                | Yes — `cloudflared` daemon, new dependency                                                                                                                                                                                                        |
| Vendor lock-in                    | Proprietary, closed relay, Synology-account-gated                                 | Fully open, self-hosted, no vendor gate                               | Proprietary edge, though the `cloudflared` binary is open-source                                                                                                                                                                                  |
| Track record                      | Multiple relay-specific CVEs, 2020-2026, one demonstrated on this exact NAS model | Depends entirely on own patch/config discipline                       | No CVEs found specific to Tunnel's core relay mechanism in this research; documented risk is attacker abuse of `cloudflared` _after_ a host is already compromised (GuidePoint Security), a different threat model than deploying it deliberately |

## 6. Privacy

QuickConnect requires a Synology Account and transmits NAS serial number, IP, and routing ports to Synology's infrastructure — a materially different posture from the current Caddy setup, which involves no third-party account or vendor telemetry. This data leaves Swiss/EU jurisdiction to a Taiwan-headquartered vendor — a data-sovereignty consideration, not a GDPR/nLPD compliance obligation for household use.

## Risk summary

QuickConnect is not a passive NAT-punching helper that disappears once connected — it's a standing, Synology-operated, TLS-terminating reverse proxy with an account/serial-based identity system. Its relay mechanism has had at least two mechanism-specific critical CVEs (2021, 2022 — the 2022 one demonstrated on this exact NAS model) and one recent (2026) interception disclosure. The "end-to-end encryption" marketing claim doesn't hold at the packet level for the relay hop, per Synology's own documentation. Enabling it also makes the NAS passively enumerable via public CT logs, independent of link secrecy.

The instinct to distrust it as a "black box" was directionally right on a specific technical point (the relay isn't truly end-to-end despite the marketing), though the actual documented incident history is more scoped (Photos-app RCE + relay-impersonation flaws, both since patched) than "compromised black box" implies.

What would need to be true for QuickConnect to be reasonable here: accepting the relay as a plaintext-capable intermediary for that hop; scoping to Photos-only (reduces but doesn't eliminate exposure); keeping DSM patched promptly; accepting the NAS becomes publicly enumerable regardless of link secrecy; and accepting a Synology-Account-gated posture for this one low-stakes use case, in exchange for zero new infrastructure.
